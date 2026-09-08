# Deploying to Railway

The app runs as a Streamlit web service. Railway builds it with Nixpacks
straight from `requirements.txt` — there is no Dockerfile to maintain.

---

## Before you start

**Rotate the API key you pasted into chat.** Any key that has been sent through
a chat window, a ticket, or an email should be considered public. Create a fresh
one at [console.anthropic.com](https://console.anthropic.com/settings/keys),
paste *that* into Railway, and revoke the old one. Railway stores its own copy
as an environment variable, so rotating later costs one paste and a restart —
no code change, no redeploy.

**Set a spend limit.** Console → Settings → Limits. A deployed URL with a live
key behind it can spend money without you watching. A full analysis is roughly
50–60k tokens (about **$1 per report** on `claude-opus-5`).

**Never commit `.env`.** It is gitignored, and `tests/test_deployment.py`
fails the build if an `sk-ant-…` key appears in any committed file.

---

## 1. Push the repository

```bash
git init                      # if this is not already a repo
git add .
git commit -m "Competitive intelligence one-pager generator"
git branch -M main
git remote add origin https://github.com/<you>/<repo>.git
git push -u origin main
```

Confirm `.env` is *not* in the commit:

```bash
git ls-files | grep -c "^\.env$"     # must print 0
```

## 2. Create the Railway service

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
2. Select the repository. Railway detects Python and reads `railway.toml`.
3. The first build takes 2–4 minutes.

There is nothing to configure in the build: `.python-version` pins Python 3.12,
`requirements.txt` supplies the dependencies, and `railway.toml` supplies the
start command and health check.

## 3. Set the environment variables

Service → **Variables** → **Raw Editor**, then paste:

```
ANTHROPIC_API_KEY=<paste-your-fresh-anthropic-key>
APP_PASSWORD=choose-a-strong-password
CI_MODEL=claude-opus-5
CI_EFFORT=high
CI_RESEARCH_ENABLED=true
CI_MATRIX_ROWS=6

RESEND_API_KEY=<paste-your-fresh-resend-key>
EMAIL_ENABLED=true
EMAIL_TO=Info@tencapital.group
EMAIL_FROM=TEN Capital Decision Intelligence <reports@tencapital.group>
```

| Variable | Required | Notes |
| --- | :---: | --- |
| `ANTHROPIC_API_KEY` | **yes** | Without it the app runs in heuristic mode and every report is stamped `DEGRADED RUN` |
| `APP_PASSWORD` | strongly advised | Gates the UI. Unset, the app displays a visible warning that anyone with the URL can spend your credits |
| `CI_MODEL` | no | `claude-opus-5` default. `claude-sonnet-5` is roughly 2.5× cheaper and noticeably faster |
| `CI_EFFORT` | no | `high` default. `medium` cuts run time and cost with modest quality loss |
| `CI_RESEARCH_ENABLED` | no | `true` default. Per-run user consent is still required in the UI |
| `TEN_CRM_ENDPOINT` | no | Leave unset unless you want the CRM push button to work |
| `RESEND_API_KEY` | for email | Without it the app runs normally but sends nothing |
| `EMAIL_TO` | no | `Info@tencapital.group` default; comma-separated for several |
| `EMAIL_FROM` | no | **Must be on a domain verified in Resend** or the send is rejected |
| `EMAIL_ENABLED` | no | `true` default; set `false` to keep the key but stop sending |

Do **not** set `PORT` — Railway injects it.

## 4. Verify email delivery

`tencapital.group` is already verified in Resend, so `reports@tencapital.group`
is a valid sender. Confirm the whole path before relying on it:

```bash
python cli.py --email-test
```

That sends one `[TEST]`-prefixed email using the blank document template, with
both attachments. It makes **no model call and costs nothing** — it exercises
the Resend credentials, the sender domain and the attachment encoding only.

If you add a new sending domain later, verify it at
[resend.com/domains](https://resend.com/domains) before pointing `EMAIL_FROM`
at it; an unverified sender returns a 422 and the run records a warning.

## 5. Generate the public URL

Service → **Settings** → **Networking** → **Generate Domain**.

Open it, enter your `APP_PASSWORD`, and run a deck.

---

## Expected behaviour

**A full run takes 6–10 minutes** at `claude-opus-5` / `high`. That is normal:
the pipeline makes 8 model calls, several of which perform live web research.
The progress bar names the current stage throughout. Streamlit holds the
websocket open, so the browser tab must stay open — closing it abandons the run.

To trade quality for speed, set `CI_EFFORT=medium` (roughly halves the time) or
`CI_MODEL=claude-sonnet-5`.

**Storage is ephemeral.** Generated PDFs and JSON records are written to the
container's disk and served to the browser as downloads. They do not survive a
restart, and they are not meant to — the browser download is the deliverable.

**One replica.** `numReplicas = 1` is deliberate: an analysis is a single
long-lived websocket session, and a rolling restart mid-run would abandon it.

---

## Verifying a deployment

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://<your-app>.up.railway.app/_stcore/health
# 200
```

Then in the UI: upload `samples/sample_pitch_deck.pptx`, tick consent, and
generate. You should get a one-page PDF and a JSON record.

If the model is not reachable, the app does not fail — it produces a report
marked `DEGRADED RUN` with a warning naming the cause. If you see that banner,
check `ANTHROPIC_API_KEY` in Variables.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Health check fails, service restarts | App bound to the wrong port | Confirm `startCommand` uses `$PORT` and `0.0.0.0`; do not set `PORT` yourself |
| File uploads fail silently | Streamlit's XSRF/CORS checks behind Railway's proxy | Already handled in `.streamlit/config.toml`; confirm that file was committed |
| "No Anthropic credential found" | Variable missing or misnamed | It must be exactly `ANTHROPIC_API_KEY` |
| Upload rejected as too large | Deck exceeds the limit | Raise `maxUploadSize` in `.streamlit/config.toml` |
| Run dies partway | Browser tab closed, or service restarted | Keep the tab open; check Railway's deploy logs |
| Build fails on a dependency | Python version mismatch | `.python-version` pins 3.12; the code needs 3.10+ |
| Tab icon missing after deploy | `static/` or `assets/` not committed | Both directories must be in the repo; check `git ls-files static assets` |
| No email arrives | Key, recipient or sender domain | Run `python cli.py --email-test`; check Resend's dashboard logs |
| Email rejected with HTTP 422 | `EMAIL_FROM` domain not verified in Resend | Verify the domain, or send from one that is |
| Report produced but no email | Analyst unticked the box, or `EMAIL_ENABLED=false` | Use the **Email report** button on the results page |

Logs: Railway service → **Deployments** → **View Logs**. The app logs each
pipeline stage and every model call's token usage.

---

## Cost control

- **Set a spend limit in the Anthropic Console.** This is the only hard stop.
- Keep `APP_PASSWORD` set. It is the difference between your key serving you and
  serving the internet.
- `CI_EFFORT=medium` or `CI_MODEL=claude-sonnet-5` materially reduce per-run
  cost; both are environment variables, so changing them is a restart, not a
  redeploy.
- Railway itself sleeps inactive services on the free tier. The first request
  after a sleep takes a few seconds to wake.

## Running the CLI against the deployed configuration

The CLI and the web app share one pipeline, so anything the app does can be
scripted locally:

```bash
python cli.py deck.pdf --research           # full analysis
python cli.py --template                    # blank document template, no API cost
```
