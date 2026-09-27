import os
import json
import anthropic

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

ANALYSIS_JSON_SHAPE = """{
 "overall_score": 0,
 "scores": {"ats":0,"jd_match":0,"content_quality":0,"bullet_quality":0,"impact":0,"ocr":0},
 "ocr_note": "",
 "career_framework": [{"letter":"C","label":"Contact Information","status":"good|warning|missing","note":""}],
 "keyword_analysis": {
   "missing_must_have": [{"term":"","note":""}],
   "matches": [{"term":"","match_type":"EXACT_MATCH|SEMANTIC_MATCH|RELATED|MISSING","evidence":""}]
 },
 "repetition_analysis": [{"word":"","count":0,"note":""}],
 "power_verbs": [{"weak_phrase":"","suggested":"","context":""}],
 "bullet_analysis": [{"original":"","classification":"Strong|Moderate|Weak","reason":"","improvement":""}],
 "formatting_analysis": [{"issue":"","severity":"HIGH|MEDIUM|LOW|GOOD","note":""}],
 "ats_platform_checks": [{"platform":"Greenhouse|Workday|iCIMS|Taleo|Lever|SAP SuccessFactors|BambooHR|ADP","risk":"HIGH|MEDIUM|LOW","note":""}],
 "star_analysis": [{"bullet":"","situation":0,"task":0,"action":0,"result":0}],
 "priority_actions": [{"severity":"HIGH|MEDIUM|GOOD","text":""}],
 "summary": ""
}"""

ANALYSIS_PROMPT = """You are an expert ATS Resume Analyzer, OCR Resume Quality Analyst, Recruiter, Resume Writer, and Job Description Matching Specialist.

Analyze the candidate resume and produce an objective, evidence-based report. Never invent candidate experience, skills, metrics, certifications, employers, achievements, or qualifications. Never invent an OCR confidence value.

{jd_instruction}

CAREER FRAMEWORK: C=Contact, A=About, R=Relevant Skills, E=Experience, E=Education & Certifications, R=Recognitions & Results.

Detect repeated action verbs/phrases (ignore stop words) with frequency.
For each experience bullet (up to 8 most relevant — pick the strongest/most relevant ones if there are more), evaluate action verb strength, situation/task, action, result, quantification, relevance to the target, clarity, and classify Strong/Moderate/Weak with a one-line reason (max ~20 words) and one short, one-line improvement suggestion (never invent metrics — only suggest the TYPE of metric missing). Keep every "note"/"reason"/"evidence" field in this whole report to one concise sentence — this is a dashboard, not an essay; brevity across every field matters more than covering every possible detail.
Detect weak openings (Responsible for, Worked on, Helped with, Involved in, Participated in) and suggest stronger context-appropriate verbs without changing factual meaning.
Detect ATS formatting risks (tables, columns, images, icons, unusual headings, inconsistent dates) only if inferable from the text given.
No actual OCR was run (this is extracted/pasted text); set ocr_note accordingly.

ATS_PLATFORM_CHECKS: You cannot actually run the resume through Greenhouse, Workday, iCIMS, Taleo, Lever, SAP SuccessFactors, BambooHR, or ADP's real parsers — you do not have access to them. Instead, evaluate the resume's TEXT STRUCTURE against each platform's well-documented public parsing weaknesses and give a risk rating grounded only in what you can see in the text (e.g. table-like whitespace patterns, inconsistent date formats, unusual section headers, non-standard bullet characters). Be explicit that this is a compatibility estimate based on known parsing behavior patterns, not a guarantee, since these are closed-source, frequently-updated commercial systems. Cover exactly 4 platforms (Workday, Greenhouse, iCIMS, Taleo) with one short sentence each — do not add more platforms.

Weights: ATS Compatibility 20%, JD Match 25%, Content Quality 20%, Bullet Quality 15%, Impact & Quantification 10%, OCR Compatibility 10%. (When no job description was given, "JD Match" instead measures general fit against the stated target role's typical expectations — say so in that score's context via the summary.)

Return ONLY valid JSON (no markdown fences, no prose) matching exactly this shape:
{json_shape}

RESUME:
\"\"\"{resume}\"\"\"

TARGET_ROLE: {role}
"""

REWRITE_PROMPT = """You are an expert resume writer producing a rewrite optimized for maximum compatibility across major ATS platforms (Workday, Greenhouse, iCIMS, Taleo, Lever, SAP SuccessFactors, BambooHR, ADP) and for OCR-based text extraction.

{jd_instruction}

STRICT RULES:
- Use ONLY facts, employers, titles, dates, tools, and achievements already present in the ORIGINAL RESUME below. Never invent metrics, numbers, employers, titles, tools, or responsibilities.
- If a bullet lacks a measurable result, keep it factual and clear rather than adding a fake number.
- Structure: plain-text, single-column, reverse-chronological. Standard section headings in capitals: CONTACT, SUMMARY, SKILLS, EXPERIENCE, EDUCATION, CERTIFICATIONS (omit sections not present in the original).
- No tables, columns, text boxes, icons, images, graphics, headers/footers, or special bullet glyphs (use a plain "- " hyphen for every bullet). This single-column plain-text structure is what maximizes parsing reliability across Workday, Greenhouse, iCIMS, Taleo, Lever and similar systems, since none of them can reliably read multi-column layouts, text boxes, or embedded graphics.
- Consistent date format (e.g. Jan 2022 - Present) — ambiguous or inconsistent dates are a common cause of an ATS silently misreading a candidate's work history.
- Standard, spelled-out section headings and job titles (avoid cute/unusual headers) since older parsers like Taleo and some iCIMS configurations rely on keyword-matched headers.
- Every experience bullet should start with a strong action verb and favor an Action -> Result structure.
- Avoid unusual characters or decorative separators that break text extraction or OCR (these create ATS "black holes" where content is silently dropped).

Return ONLY valid JSON, no markdown fences:
{{"resume_text": "the full rewritten resume as plain text with \\n line breaks", "changes_summary": "3-6 short bullet points separated by \\n explaining what changed and why", "compatibility_note": "one or two honest sentences noting this maximizes compatibility with known parsing behavior across major ATS platforms but is not a guaranteed bypass of any specific vendor's ranking algorithm"}}

ORIGINAL RESUME:
\"\"\"{resume}\"\"\"

TARGET_ROLE: {role}

PRIOR ANALYSIS FINDINGS (guide the fixes, do not treat as new facts):
{findings}
"""


def _jd_context(jd: str, role: str) -> str:
    """The generic 'here's the JD, or here's just a role' block, with no
    analysis-specific JSON field instructions — safe to reuse anywhere."""
    jd = (jd or "").strip()
    if jd:
        return f'JOB_DESCRIPTION:\n"""{jd}"""'
    return (
        f"No specific job description was provided — only a target role/title ({role or 'not specified'}). "
        "Do NOT invent a fake job description. Work from general, well-known expectations for that role/title "
        "in the current job market instead."
    )


def _jd_instruction(jd: str, role: str) -> str:
    """Analysis-specific version: adds the keyword_analysis JSON-field instructions
    on top of the generic JD context. Used only by analyze_resume()."""
    jd = (jd or "").strip()
    if jd:
        return (
            f'A specific job description was provided. For keyword_analysis.matches, classify each JD term as '
            f'EXACT_MATCH, SEMANTIC_MATCH, RELATED, or MISSING, with the evidence phrase from the resume.\n\n'
            f'{_jd_context(jd, role)}'
        )
    return (
        f"{_jd_context(jd, role)} Evaluate the resume against those general expectations, and populate "
        "keyword_analysis.matches/missing_must_have against them (match_type EXACT_MATCH/SEMANTIC_MATCH/"
        "RELATED/MISSING still applies, just against typical-for-the-role terms rather than a specific "
        "posting). Be explicit in the summary that this is a general-fit read, not a match against a "
        "specific employer's posting, and that results will be more precise if the person adds a real job "
        "description later."
    )


def _call_json(prompt: str) -> dict:
    if client is None:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured on the server.")
    resp = client.messages.create(
        model=MODEL,
        # A full analysis JSON (up to 12 bullets, 8 ATS platform checks, full keyword matching,
        # career framework, etc.) against a detailed resume + JD can genuinely need this much —
        # 4000 and even 8000 were both observed truncating mid-response on real detailed resumes.
        # 16000 gives real headroom without being wastefully high.
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        truncated = resp.stop_reason == "max_tokens"
        if truncated:
            raise RuntimeError(
                "The AI's response was too long and got cut off before finishing (this resume/JD combo "
                "produced an unusually detailed report). Try again, or shorten the resume/job description text."
            ) from e
        raise RuntimeError(f"The AI returned malformed JSON we couldn't parse: {e}") from e


def analyze_resume(resume: str, jd: str, role: str) -> dict:
    prompt = ANALYSIS_PROMPT.format(
        json_shape=ANALYSIS_JSON_SHAPE,
        resume=resume[:12000],
        role=role or "(not specified)",
        jd_instruction=_jd_instruction(jd, role),
    )
    return _call_json(prompt)


def rewrite_resume(resume: str, jd: str, role: str, findings: dict) -> dict:
    prompt = REWRITE_PROMPT.format(
        resume=resume[:12000],
        role=role or "(not specified)",
        findings=json.dumps(findings)[:4000],
        jd_instruction=_jd_context(jd, role),
    )
    return _call_json(prompt)


# ============================================================================
# Job recommendations and interview prep are deliberately SEPARATE calls from
# analyze_resume(), not extra fields bolted onto it. Two reasons: (1) it keeps
# the main analysis JSON small and reliable (we already hit a truncation bug
# once from an over-large single response — see _call_json's comment), and
# (2) it makes each feature independently cacheable/reusable/quota-gateable
# later without having to touch the analysis prompt at all.
# ============================================================================

JOB_RECS_PROMPT = """You are a career advisor. Based ONLY on the skills, experience, and seniority actually
shown in this resume, suggest realistic next-step job titles this candidate is genuinely qualified for
right now. Do not suggest roles that would require skills or experience not evidenced in the resume.
Prefer a mix: 2-3 roles very close to their current trajectory, and 1-2 adjacent/stretch roles that are
still a reasonable, evidence-based next step (not aspirational fantasy roles).

Return ONLY valid JSON, no markdown fences:
{{"recommendations": [{{"title": "", "why_fit": "one concise sentence grounded in specific resume evidence", "stretch_level": "close_match|stretch"}}]}}
Return 4-6 recommendations total.

RESUME:
\"\"\"{resume}\"\"\"

TARGET_ROLE (if given, weight recommendations near this): {role}
"""

INTERVIEW_PREP_PROMPT = """You are an interview coach. Generate realistic interview questions this candidate should
prepare for, based on their actual resume and (if given) the target role/job description. Mix question
types: a few behavioral (STAR-style), a few role/technical-competency questions relevant to their actual
experience, and 1-2 questions specifically probing any gap or weak spot you'd genuinely expect an
interviewer to probe given this resume (e.g. a career gap, a short stint, a claimed skill with thin
evidence) — frame these constructively, not as "gotchas."

For each question, give a short prep_tip: NOT a scripted answer, but concrete guidance on what evidence
from their OWN background (reference it specifically) they should structure an answer around.

Return ONLY valid JSON, no markdown fences:
{{"questions": [{{"question": "", "type": "behavioral|technical|gap_probe", "prep_tip": "one or two concise sentences"}}]}}
Return 6-8 questions total.

RESUME:
\"\"\"{resume}\"\"\"

{jd_instruction}
"""


def job_recommendations(resume: str, role: str) -> dict:
    prompt = JOB_RECS_PROMPT.format(resume=resume[:12000], role=role or "(not specified)")
    return _call_json(prompt)


def interview_prep(resume: str, jd: str, role: str) -> dict:
    prompt = INTERVIEW_PREP_PROMPT.format(
        resume=resume[:12000],
        jd_instruction=_jd_context(jd, role),
    )
    return _call_json(prompt)
