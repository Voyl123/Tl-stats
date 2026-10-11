#!/usr/bin/env python3
"""
Auto-upload match leaderboards to GitHub Pages.

Watches the results folder and automatically commits new matches to the gh-pages branch.
Run this alongside your TL Scoreboard app, or set it as a scheduled task.

Setup:
  1. Create a GitHub Personal Access Token (PAT):
     - Go to https://github.com/settings/tokens/new
     - Select: repo (full), workflow (optional)
     - Copy the token
  
  2. Save config to auto_upload.json:
     {
       "repo": "Voyl123/Tl-stats",
       "token": "github_pat_xxx...",
       "branch": "gh-pages",
       "results_dir": "results",
       "auto_commit": true
     }
  
  3. Run: python auto_upload.py

The script monitors results/ and commits new matches every few seconds.
"""

import json
import time
import base64
import sys
from pathlib import Path
from datetime import datetime
from urllib.parse import urlencode
import urllib.request
import urllib.error
from hashlib import md5

CONFIG_FILE = Path("auto_upload.json")
RESULTS_DIR = Path("results")
GITHUB_API = "https://api.github.com"

# Track uploaded matches to avoid re-uploading
UPLOADED_FILE = Path(".uploaded_matches")


class GitHubUploader:
    def __init__(self, repo, token, branch="gh-pages"):
        self.repo = repo
        self.token = token
        self.branch = branch
        self.owner, self.name = repo.split("/", 1)

    def _request(self, method, endpoint, data=None, ok_codes=(200, 201, 422)):
        """Make a GitHub API request."""
        url = f"{GITHUB_API}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "tl-stats-uploader"
        }
        
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code in ok_codes:
                try:
                    return e.code, json.loads(e.read())
                except:
                    return e.code, {}
            print(f"❌ GitHub API error {e.code}: {e.reason}")
            return e.code, {}

    def _ensure_branch(self):
        """Ensure gh-pages branch exists."""
        code, _ = self._request("GET", f"/repos/{self.repo}/branches/{self.branch}")
        if code == 200:
            return True
        
        # Try to create it from main
        code, repo = self._request("GET", f"/repos/{self.repo}")
        if code != 200:
            print(f"❌ Can't access repo: {repo.get('message')}")
            return False
        
        default_branch = repo.get("default_branch", "main")
        code, ref = self._request("GET", f"/repos/{self.repo}/git/ref/heads/{default_branch}")
        if code != 200:
            print(f"❌ Can't read {default_branch} branch")
            return False
        
        sha = ref.get("object", {}).get("sha")
        if not sha:
            return False
        
        code, _ = self._request(
            "POST",
            f"/repos/{self.repo}/git/refs",
            {"ref": f"refs/heads/{self.branch}", "sha": sha}
        )
        return code in (201, 422)  # 422 = already exists

    def upload_file(self, local_path, github_path, message=""):
        """Upload or update a single file."""
        if not local_path.exists():
            return False
        
        content = local_path.read_bytes()
        encoded = base64.b64encode(content).decode()
        
        # Get current file SHA if it exists
        code, cur = self._request("GET", f"/repos/{self.repo}/contents/{github_path}?ref={self.branch}")
        sha = cur.get("sha") if code == 200 else None
        
        body = {
            "message": message or f"Add {github_path}",
            "content": encoded,
            "branch": self.branch
        }
        if sha:
            body["sha"] = sha
        
        code, res = self._request("PUT", f"/repos/{self.repo}/contents/{github_path}", body)
        
        if code in (200, 201):
            return True
        
        print(f"⚠️  Failed to upload {github_path}: {res.get('message', code)}")
        return False

    def upload_directory(self, local_dir, github_prefix="m/"):
        """Upload all files from a results match folder."""
        local_dir = Path(local_dir)
        if not local_dir.is_dir():
            return False
        
        match_name = local_dir.name
        files = [
            ("leaderboard.html", f"{github_prefix}{match_name}/index.html"),
            ("leaderboard.json", f"{github_prefix}{match_name}/leaderboard.json"),
            ("leaderboard.csv", f"{github_prefix}{match_name}/leaderboard.csv"),
            ("discord.md", f"{github_prefix}{match_name}/discord.md"),
        ]
        
        uploaded_count = 0
        for local_name, github_path in files:
            local_file = local_dir / local_name
            if local_file.exists():
                if self.upload_file(local_file, github_path, f"Update {match_name}"):
                    uploaded_count += 1
        
        return uploaded_count > 0

    def update_index(self):
        """Regenerate index.html with all matches."""
        matches = []
        m_dir = RESULTS_DIR.parent / "site" / "m"
        if not m_dir.exists():
            m_dir = RESULTS_DIR.parent / "m"
        
        if m_dir.exists():
            for info_file in sorted(m_dir.glob("*/.info.json"), reverse=True):
                try:
                    info = json.loads(info_file.read_text())
                    info["id"] = info_file.parent.name
                    matches.append(info)
                except:
                    pass
        
        html = self._generate_index(matches)
        
        # Upload index
        index_path = Path("index.html")
        index_path.write_text(html)
        return self.upload_file(index_path, "index.html", "Update leaderboard index")

    def _generate_index(self, matches):
        """Generate index.html content."""
        total_players = sum(m.get("players", 0) for m in matches)
        
        matches_html = ""
        if matches:
            for m in matches[:50]:  # Show last 50
                title = m.get("label", f"Match {m.get('id', '?')}")
                date = m.get("meta", {}).get("scanned", "Unknown")
                players = m.get("players", 0)
                teams = m.get("teams", {})
                team_list = " • ".join(f"{t}: {d.get('players', 0)}" for t, d in teams.items())
                
                matches_html += f"""
                    <a href="m/{m['id']}/" class="match-card">
                        <div class="match-title">{title}</div>
                        <div class="match-meta">{date}</div>
                        <div class="match-stats">
                            <span class="match-stat">{players} players</span>
                            {f'<span class="match-stat">{team_list}</span>' if team_list else ''}
                        </div>
                    </a>
                """
        else:
            matches_html = '<div class="empty-state"><p>No matches yet</p></div>'
        
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Supple Stats - Leaderboards</title>
<style>
:root{{color-scheme:dark;--bg:#0d0f15;--panel:#141824;--text:#ece9e1;--gold:#d8b46a}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}}
header{{background:linear-gradient(135deg,rgba(20,24,36,.95),rgba(13,15,21,.95));border-bottom:1px solid #262d3f;padding:2rem;text-align:center}}
h1{{margin:0;font-size:28px;color:#f3d79b}}main{{max-width:1200px;margin:0 auto;padding:2rem}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:1rem;margin-bottom:3rem}}
.stat-card{{background:var(--panel);border:1px solid #262d3f;border-radius:12px;padding:1.5rem}}
.stat-label{{color:#8d8a80;font-size:12px;text-transform:uppercase;margin-bottom:.5rem}}
.stat-value{{font-size:32px;font-weight:800;color:#f3d79b}}
.matches{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:1rem}}
.match-card{{background:var(--panel);border:1px solid #262d3f;border-radius:12px;padding:1.5rem;text-decoration:none;color:inherit;transition:all .15s}}
.match-card:hover{{border-color:var(--gold);transform:translateY(-2px)}}
.match-title{{font-weight:700;font-size:16px;color:#f3d79b;margin-bottom:.5rem}}
.match-meta{{font-size:13px;color:#8d8a80;margin-bottom:1rem}}
.match-stats{{display:flex;gap:1rem;flex-wrap:wrap;font-size:12px}}
.match-stat{{padding:.25rem .75rem;background:rgba(216,180,106,.1);border-radius:6px;color:var(--gold);font-weight:600}}
.empty-state{{background:var(--panel);border:1px dashed #262d3f;border-radius:12px;padding:2rem;text-align:center;color:#8d8a80}}
</style></head><body>
<header><h1>⚔️ Supple Stats</h1><p style="margin:0.5rem 0 0;color:#8d8a80">Throne and Liberty Match Leaderboards</p></header>
<main>
<section><h2>Statistics</h2>
<div class="stats">
<div class="stat-card"><div class="stat-label">Total Matches</div><div class="stat-value">{len(matches)}</div></div>
<div class="stat-card"><div class="stat-label">Total Players</div><div class="stat-value">{total_players}</div></div>
<div class="stat-card"><div class="stat-label">Last Updated</div><div class="stat-value">{datetime.now():%b %d}</div></div>
</div></section>
<section><h2>Recent Matches</h2><div class="matches">{matches_html}</div></section>
</main></body></html>"""


def load_config():
    """Load configuration."""
    if not CONFIG_FILE.exists():
        print(f"""
❌ Config file not found: {CONFIG_FILE}

Create {CONFIG_FILE} with:
{{
  "repo": "Voyl123/Tl-stats",
  "token": "github_pat_xxx...",
  "branch": "gh-pages",
  "results_dir": "results",
  "auto_commit": true
}}

Get a token: https://github.com/settings/tokens/new
""")
        return None
    
    try:
        return json.loads(CONFIG_FILE.read_text())
    except:
        print(f"❌ Invalid JSON in {CONFIG_FILE}")
        return None


def get_uploaded_matches():
    """Get set of already uploaded match IDs."""
    if UPLOADED_FILE.exists():
        try:
            return set(json.loads(UPLOADED_FILE.read_text()).get("uploaded", []))
        except:
            pass
    return set()


def save_uploaded_match(match_id):
    """Mark a match as uploaded."""
    uploaded = get_uploaded_matches()
    uploaded.add(match_id)
    UPLOADED_FILE.write_text(json.dumps({"uploaded": sorted(uploaded), "updated": datetime.now().isoformat()}))


def find_new_matches(results_dir):
    """Find leaderboard folders with observations.json (successfully scanned matches)."""
    results_path = Path(results_dir)
    matches = []
    
    if results_path.exists():
        for obs_file in results_path.glob("**/observations.json"):
            match_dir = obs_file.parent
            match_id = match_dir.name
            if match_id.startswith("_"):  # Skip temp dirs
                continue
            matches.append(match_dir)
    
    return sorted(matches, key=lambda p: p.stat().st_mtime, reverse=True)


def monitor_and_upload(config):
    """Main loop: watch for new matches and upload them."""
    repo = config.get("repo")
    token = config.get("token")
    branch = config.get("branch", "gh-pages")
    results_dir = config.get("results_dir", "results")
    interval = config.get("interval", 5)  # Check every 5 seconds
    
    if not token:
        print("❌ No GitHub token in config")
        return
    
    uploader = GitHubUploader(repo, token, branch)
    
    # Ensure branch exists
    print(f"📦 Setting up {repo} ({branch} branch)...")
    if not uploader._ensure_branch():
        print("❌ Failed to set up GitHub branch")
        return
    
    print(f"✅ Connected to {repo}")
    print(f"📁 Monitoring {results_dir}/ for new matches...")
    print("Press Ctrl+C to stop\n")
    
    uploaded = get_uploaded_matches()
    last_index_update = 0
    
    try:
        while True:
            try:
                matches = find_new_matches(results_dir)
                
                # Upload new matches
                for match_dir in matches:
                    match_id = match_dir.name
                    
                    if match_id in uploaded:
                        continue
                    
                    print(f"📤 Uploading {match_id}...")
                    if uploader.upload_directory(match_dir):
                        save_uploaded_match(match_id)
                        uploaded.add(match_id)
                        print(f"   ✅ Uploaded to https://voyl123.github.io/Tl-stats/m/{match_id}/")
                    else:
                        print(f"   ⚠️  Failed to upload {match_id}")
                
                # Update index every 30 seconds or after uploading
                now = time.time()
                if now - last_index_update > 30 or (matches and matches[0].name not in uploaded):
                    print("🔄 Updating index...")
                    if uploader.update_index():
                        last_index_update = now
                        print("   ✅ Index updated")
                
                time.sleep(interval)
            except Exception as e:
                print(f"⚠️  Error: {e}")
                time.sleep(interval)
    
    except KeyboardInterrupt:
        print("\n👋 Stopped")


def main():
    config = load_config()
    if not config:
        return
    
    if "--test" in sys.argv:
        # Just test connectivity
        uploader = GitHubUploader(config["repo"], config["token"], config.get("branch", "gh-pages"))
        code, res = uploader._request("GET", f"/user")
        if code == 200:
            print(f"✅ GitHub authenticated as {res.get('login')}")
            print(f"📦 Repo: {config['repo']}")
            print(f"🌿 Branch: {config.get('branch', 'gh-pages')}")
        else:
            print(f"❌ Auth failed: {res.get('message')}")
        return
    
    monitor_and_upload(config)


if __name__ == "__main__":
    main()
