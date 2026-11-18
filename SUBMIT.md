# Submission guide (do these from your own accounts)

## 1. Push to GitHub (your account)
Create an empty **public** repo on github.com (no README/license), then in PowerShell in this folder:

    git add -A
    git commit -m "Finalize link analysis API"
    git branch -M main
    git remote add origin https://github.com/<your-username>/ai-link-analysis.git
    git push -u origin main

`.env` (with your key) is git-ignored – confirm with `git ls-files | findstr .env` (should list only `.env.example`).

## 2. Deploy
Render → New → Blueprint → pick the repo → set `NVIDIA_API_KEY` → deploy. Then open `<url>/docs` and test `POST /link-analysis`.

## 3. Follow (verification step)
- LinkedIn: https://www.linkedin.com/company/dxora-technologies
- Instagram: https://www.instagram.com/crestspot/

## 4. Email
To: hr@chivoxai.com
Subject: <Your Name> - Python Practical Link Analysis test review

1. Full name
2. Demo link: https://blocked-readers-miss-joy.trycloudflare.com  (docs: https://blocked-readers-miss-joy.trycloudflare.com/docs, endpoint: https://blocked-readers-miss-joy.trycloudflare.com/link-analysis) - temporary Cloudflare tunnel running from my PC (free hosts asked for a card); I can restart it any time
3. GitHub repo: https://github.com/<your-username>/ai-link-analysis
4. Contact number
5. Current location
6. Preferred date and time for final discussion
(+ your LinkedIn and Instagram profile links)

## 5. Afterwards
Regenerate your NVIDIA API key on build.nvidia.com (it was shared in a chat).
