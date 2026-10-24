# HCM Lite — AI-assisted Human Capital Management

A Python + Streamlit + SQLite hackathon starter implementation based on the Saylani HCM Lite challenge brief.

## Included in this starter

- First-run company setup and Admin account creation
- Password hashing, login/logout, Admin/HR/Employee role navigation
- Company settings, departments, designations, leave types and holidays
- HR account creation and employee registration (each employee gets a login)
- Employee attendance check-in/out and attendance correction requests
- Leave requests, working-day calculations, overlap checks, balance validation and HR/ Admin approval
- Monthly payroll draft, illustrative tax calculation, Admin finalization/locking, PDF payslips
- Role-specific dashboards, charts and CSV export
- AI assistant with safe built-in analytics answers and optional OpenAI-compatible API
- Announcements, in-app notifications and Admin audit log

## Requirements

- Python 3.10 or newer recommended
- Internet only if using a hosted AI API or installing packages

## Run locally (Windows)

1. Extract the ZIP.
2. Open the extracted `hcm_lite` folder in VS Code.
3. Open the terminal in that folder.
4. Create a virtual environment:

   ```powershell
   py -m venv .venv
   .venv\Scripts\activate
   ```

5. Install packages:

   ```powershell
   python -m pip install -r requirements.txt
   ```

6. Start the app:

   ```powershell
   streamlit run app.py
   ```

7. Open the local URL Streamlit prints. On first launch, create the company and Admin account.

## AI setup (optional)

Set environment variables before starting Streamlit. Never put real API keys in source control.

PowerShell example:

```powershell
$env:AI_API_KEY="your-real-key"
$env:AI_BASE_URL="https://api.openai.com/v1"
$env:AI_MODEL="gpt-4o-mini"
streamlit run app.py
```

You can run without an AI key; a limited built-in, rule-based assistant answers common HR questions. Hosted AI is optional in this starter and needs a valid provider key.

## Important limitations before real use

This is a hackathon starter, not production HR software.

- The required core workflow is implemented, but it is not a fully audited implementation of all 25 rubric items.
- Smart leave currently provides a normal form rather than reliably parsing natural-language dates into a confirmed pre-filled request.
- CSV import, camera/face detection, GPS geofencing, attrition prediction, automatic absent-day generation, and full automated test coverage are not implemented.
- The payroll implementation uses the illustrative formulas in the challenge brief. Verify calculations with the judges' expected interpretation before presenting it as real payroll software.
- Payroll deductions and tax are simplified; approved leave overlapping a month is not prorated across month boundaries in this starter.
- The app uses SQLite for ease of setup. For multi-user hosted deployment, move to PostgreSQL and use a persistent disk/database service.
- Before public deployment, review Streamlit session security, CSRF/session handling, secrets, account recovery, authorization tests and data retention. Never use real employee personal data during development.
- Never commit `.env` or a real API key. Commit `.env.example` only.

## Suggested next improvements

1. Add unit tests for leave, attendance and payroll rules.
2. Add CSV dry-run import/export.
3. Add attendance auto-status generation for employees with no record.
4. Improve the AI integration to use backend tool calling with explicit role-scoped functions.
5. Add smart leave parsing that returns validated JSON and requires employee confirmation.
6. Add geofenced camera attendance only after the core system works.
7. Deploy with a persistent PostgreSQL database and document the live URL.

## Fresh install behavior

The database file `hcm_lite.db` is created automatically on first run. The company and Admin account are created through the first-run form. No employee demo records are preloaded.
