# Resume ATS & OCR Analyzer — SaaS starter

A standalone, self-hosted version of the ATS analyzer: FastAPI backend (your own Claude API key),
user accounts, a free-analysis + paid-rewrite model, and Razorpay billing (INR) so you can charge for it.

```
backend/    FastAPI app (auth, analysis, rewrite, Razorpay billing, quota enforcement)
frontend/   Single static HTML/JS site (no build step)
```

## 1. How the product is priced (as you specified)

- **Analysis (ATS/OCR score report) is always free** — no login limit, no quota.
- **The rewrite feature is paywalled.** Buying the **₹350/month plan** unlocks:
  - **100 "modify" calls** (each time the AI regenerates the ATS-safe rewrite)
  - **20 downloads** of the rewritten resume
  - Valid for **30 days** from purchase
- If either quota runs out **before** the 30 days are up, the user can buy a **top-up**
  (₹150 by default → +50 modifies / +10 downloads) **without losing remaining validity** —
  top-ups only add quota, they never extend an expired plan. If the 30 days have expired,
  the user has to buy the plan again (a fresh purchase resets quotas to 100/20 for a new 30 days).
- All of this is enforced **server-side** in `backend/main.py` (`_plan_is_live`, the quota checks
  in `/api/rewrite` and `/api/rewrite/download`) — never trust a frontend-only paywall, since anyone
  can read your JS. Every price/quota number is also a `.env` variable, so you can change them without
  touching code: `PLAN_PRICE_INR`, `PLAN_VALIDITY_DAYS`, `PLAN_MODIFY_QUOTA`, `PLAN_DOWNLOAD_QUOTA`,
  `TOPUP_PRICE_INR`, `TOPUP_MODIFY_QUOTA`, `TOPUP_DOWNLOAD_QUOTA`.

## 2. What this includes

- **Sign-in with email OR phone number**, with a tab to switch between them and a proper Create
  account / Sign in split — first-time users are required to create an account first (that tab is the
  default), and after signup they're sent to Sign In with their identifier pre-filled rather than being
  silently auto-logged-in, so it's unambiguous that they now have a login ID + password.
- A built-in email-domain suggestion dropdown (gmail.com, yahoo.com, etc.) that appears after typing
  "@", instead of relying on the browser's own (inconsistent) autofill.
- `/api/analyze` — free ATS/OCR/JD-match report, including a per-platform compatibility read
  (Workday, Greenhouse, iCIMS, Taleo, Lever, SAP SuccessFactors, BambooHR, ADP)
- `/api/rewrite` — grounded ATS/OCR-safe resume rewrite, gated by the modify quota
- `/api/rewrite/download` — records + gates a download against the download quota
- `/api/extract` — server-side PDF/DOCX text extraction (frontend also does this client-side for speed)
- `/api/billing/create-order` + `/api/billing/verify` — Razorpay one-time-order flow for both
  the monthly plan and top-ups, with server-side signature verification before any quota is granted

### On the "bypass every ATS" framing

No tool — including this one — can guarantee getting past every applicant tracking system, because
Workday, Greenhouse, iCIMS, Taleo, Lever, etc. are closed, frequently-updated commercial products with
proprietary ranking logic layered on top of parsing. What this app does, honestly: it (1) checks your
resume's text structure against the well-documented parsing weaknesses shared across those platforms
(tables, columns, text boxes, graphics, inconsistent dates, non-standard headers — the actual causes of
an ATS "black hole" where content silently drops), and (2) rewrites around them. That maximizes
compatibility across all of them at once, which is the honest, defensible version of "beat the ATS." I've
worded the in-app copy (`compatibility_note` in the rewrite response, and the platform-checks disclaimer
in the UI) to reflect that rather than promising a guaranteed bypass — recommend keeping that framing if
you write marketing copy for this, both because it's true and because overclaiming is exactly the kind of
thing that gets a product accused of being snake oil.

## 3. Run it locally

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY and JWT_SECRET at minimum
uvicorn main:app --reload --port 8000
```

**Before your first real deploy, run the config checker** — it makes real calls to Anthropic and
Razorpay and tells you exactly what's missing or broken, without ever printing your secrets back:
```bash
python3 scripts/verify_config.py
```

Then serve the frontend from `http://localhost` (don't just double-click `index.html` — opening it as a
raw `file://` path is exactly what caused the "Failed to fetch" bug in earlier testing, since the page
has no way to know where your backend is). Easiest local option:
```bash
cd frontend
python3 -m http.server 5500
# open http://localhost:5500 in your browser
```
The frontend auto-detects `localhost` and points itself at `http://localhost:8000` automatically in that case.

### If something looks broken: how to debug this codebase

- **Frontend:** open the browser's DevTools console. Every API call this page makes is logged as
  `[ATS] -> POST /api/analyze ...` (request) and `[ATS] <- 200 /api/analyze ...` (response), because
  `CONFIG.DEBUG = true` at the top of `frontend/index.html`'s `<script>` tag. Set it to `false` to quiet
  this down once you trust the deploy. If the very first thing you see is a yellow banner at the top of
  the page, that's the built-in config check telling you `CONFIG.API_BASE_URL` still has its placeholder
  value — fix that before debugging anything else.
- **Backend:** `uvicorn main:app --reload` prints every request/response to the terminal it's running in.
  FastAPI also serves interactive API docs at `http://localhost:8000/docs` — use that to call any endpoint
  by hand (with a real bearer token from `/api/login`) and see the exact JSON it returns, without needing
  the frontend at all.
- **Auth logic** lives entirely in `backend/main.py`'s `_classify_identifier()` / `register()` / `login()`
  functions — these are the ones to read first if a sign-up/sign-in bug is reported, since everything else
  (billing, analysis, rewrite) depends on a working session token from here.
- **Quota logic** lives entirely in `backend/billing.py`'s `apply_purchase()` plus the checks at the top
  of `/api/rewrite` and `/api/rewrite/download` in `main.py` — these three places are the only ones that
  touch `modify_quota` / `download_quota` / `plan_expires_at`, so a pricing bug is always in one of them.
- **To test the paid rewrite flow without a real Razorpay payment**, grant yourself a plan directly:
  `python3 -c "from database import SessionLocal, User; import billing; db=SessionLocal(); u=db.query(User).filter(User.email=='you@example.com').first(); billing.apply_purchase(u,'plan'); db.commit()"`
  (swap the filter to `User.phone=='...'` if you signed up with a phone number).

## 4. Deploy the backend (pick one — all have free/cheap tiers)

**Railway or Render (easiest):**
1. Push this folder to a GitHub repo.
2. Create a new Web Service pointing at `backend/`, build command `pip install -r requirements.txt`,
   start command `uvicorn main:app --host 0.0.0.0 --port $PORT`.
3. Add the environment variables from `.env.example` in the host's dashboard.
4. Attach a managed Postgres add-on and set `DATABASE_URL` to it (SQLite is fine for testing,
   but most hosts wipe local disk on redeploy — don't use SQLite in production).

**Fly.io / a VPS:** same idea — install requirements, run uvicorn (or gunicorn with uvicorn workers)
behind a reverse proxy (Caddy/Nginx) for HTTPS.

## 5. Deploy the frontend

`frontend/index.html` is a single static file — no build step.
1. Open it and replace `__API_BASE_URL__` with your deployed backend URL
   (e.g. `https://your-api.up.railway.app`).
2. Host it anywhere static: Netlify, Vercel, Cloudflare Pages, GitHub Pages, or your own domain's
   web server. Drag-and-drop the file onto Netlify's deploy page is the fastest path.
3. Point your domain at it, and set `FRONTEND_URL` in the backend's env vars to that domain (used for CORS).

## 6. Turn on payments (Razorpay)

Razorpay is used here because it's the standard for INR pricing and is easy to onboard as an Indian
business (Stripe stopped onboarding new India merchants for domestic charges a while back).

1. Create a Razorpay account (business KYC required before you can go live — start in **Test Mode**).
2. Copy your **Key ID** and **Key Secret** from the dashboard into `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET`.
3. That's it for the order flow used here — this starter uses Razorpay's one-time **Orders + Checkout**
   API (not their Subscriptions product), which matches your quota-based model better than a recurring
   subscription would.
4. Test with Razorpay's test mode + test card `4111 1111 1111 1111` (any future expiry/CVV) before going live.
5. Switch to live keys when ready. Razorpay settles payments to your bank account per their standard schedule.

## 7. Costs to budget for

- **Anthropic API usage** — billed per call; each analysis and each rewrite is one Claude call.
  Set a spend limit at `console.anthropic.com` so a burst of free-tier analysis traffic can't surprise you
  — remember analysis is unlimited/free in this design, so that's the cost line to watch most closely.
- **Hosting** — Railway/Render free tiers work for early testing; expect $5–20/month once you have
  real traffic and a Postgres database.
- **Razorpay** — no monthly fee, takes a % + GST per transaction (check their current pricing page).

## 8. Before you open it to the public

- Set a strong random `JWT_SECRET`.
- Switch `DATABASE_URL` to Postgres (SQLite will lose data on most hosts' redeploys).
- Add a privacy policy / terms page — you're handling people's resumes (PII), and since you're charging
  money in India you'll also want standard refund/cancellation terms visible (common Razorpay live-mode
  requirement).
- Since analysis is free and unlimited, add basic rate limiting in front of `/api/analyze` (e.g. via your
  reverse proxy or a simple per-IP/per-user cap) so it can't be hammered and run up your Anthropic bill.
- Consider adding email verification / password reset (not included in this MVP — currently anyone
  can register with any email).
- Scanned/image-only PDFs aren't OCR'd yet (`extract.py` raises a clear error asking for pasted text) —
  wire in an OCR engine (e.g. PaddleOCR, as the original PRD suggests) if that matters for your users.

## 9. Extending it

- `backend/llm.py` holds both prompts (analysis + rewrite, including the per-ATS-platform compatibility
  language) — tune them freely.
- `backend/billing.py` holds all pricing/quota logic in one place (`apply_purchase`) — this is the
  function to change if you want different plan tiers later (e.g. an annual plan, a one-off single-rewrite
  purchase for non-subscribers).
- `backend/main.py` is the whole API surface; add endpoints (e.g. save/list past reports) by adding
  a table in `database.py` and a route here.
- The frontend is one file on purpose so it's easy to reskin — feel free to move it into a proper
  React/Next app later if you want a more polished UI; the API contract stays the same.

