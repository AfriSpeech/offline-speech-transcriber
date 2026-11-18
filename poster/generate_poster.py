#!/usr/bin/env python3
"""Generates print-ready recruitment posters (HTML, PDF, PNG) from config."""

import os
import json
import base64
import argparse
import subprocess
from pathlib import Path

DEFAULT_CONFIG = {
    "project_name": "Speech AI Initiative",
    "org_name": "Your Organization",
    "partner_name": "Partner Organization",
    "eyebrow": "CALL FOR COMMUNITY VOLUNTEERS",
    "headline": "HELP BUILD LOCAL VOICE AI",
    "subhead": "Join community volunteers to verify spoken audio from authentic broadcast shows. Review audio offline on your laptop and gain official recognition.",
    "short_url": "https://bit.ly/your-signup",
    "contact_email": "submissions@yourorg.org",
    "qr_code_path": "../assets/qr_code.png",
    "logo_path": "../assets/lein_logo.png",
    "primary_color": "#07254B",
    "accent_color": "#2F6FE4"
}

def render_poster(config_data: dict, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg = {**DEFAULT_CONFIG, **config_data}

    # Load QR code base64
    qr_b64 = ""
    qr_p = Path(cfg["qr_code_path"])
    if qr_p.exists():
        with open(qr_p, "rb") as f:
            qr_b64 = base64.b64encode(f.read()).decode("utf-8")

    # Load Logo base64
    logo_b64 = ""
    logo_p = Path(cfg["logo_path"])
    if logo_p.exists():
        with open(logo_p, "rb") as f:
            logo_b64 = base64.b64encode(f.read()).decode("utf-8")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{cfg['headline']}</title>
  <style>
    @page {{ size: A4 portrait; margin: 0; }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #FAFAFA;
      color: #0F172A;
      width: 210mm;
      min-height: 297mm;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      -webkit-print-color-adjust: exact;
      print-color-adjust: exact;
    }}
    .stripe {{
      height: 7mm;
      width: 100%;
      background: linear-gradient(90deg, {cfg['primary_color']} 0 50%, {cfg['accent_color']} 50% 100%);
    }}
    .body-wrap {{
      padding: 10mm 14mm 10mm 14mm;
      display: flex;
      flex-direction: column;
      flex: 1;
      justify-content: space-between;
    }}
    .header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 2px solid #E2E8F0;
      padding-bottom: 4mm;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .logo {{
      width: 48px;
      height: 48px;
      border-radius: 8px;
      object-fit: contain;
    }}
    .hero {{
      text-align: center;
      margin: 5mm 0;
    }}
    .eyebrow {{
      display: inline-block;
      background: #F0F5FF;
      color: {cfg['accent_color']};
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      padding: 4px 14px;
      border-radius: 9999px;
      margin-bottom: 8px;
    }}
    .title {{
      font-size: 28px;
      font-weight: 900;
      color: {cfg['primary_color']};
      line-height: 1.15;
    }}
    .subhead {{
      font-size: 13.5px;
      color: #475569;
      max-width: 540px;
      margin: 8px auto 0;
      line-height: 1.45;
    }}
    .grid-2x2 {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 4mm;
      margin: 4mm 0;
    }}
    .card {{
      background: #FFFFFF;
      border: 1.5px solid #E2E8F0;
      border-radius: 10px;
      padding: 12px 14px;
    }}
    .card-title {{
      font-size: 13.5px;
      font-weight: 800;
      color: {cfg['primary_color']};
      margin-bottom: 3px;
    }}
    .card-desc {{
      font-size: 11px;
      color: #64748B;
      line-height: 1.4;
    }}
    .cta-box {{
      background: {cfg['primary_color']};
      color: #FFFFFF;
      border-radius: 12px;
      padding: 16px 20px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }}
    .url-pill {{
      background: #FFFFFF;
      color: {cfg['primary_color']};
      padding: 8px 16px;
      border-radius: 6px;
      font-family: monospace;
      font-size: 14px;
      font-weight: 800;
      display: inline-block;
      margin-top: 8px;
    }}
    .qr-wrap {{
      background: #FFFFFF;
      padding: 8px;
      border-radius: 8px;
      text-align: center;
    }}
    .qr-img {{
      width: 100px;
      height: 100px;
      object-fit: contain;
    }}
    .footer {{
      border-top: 1px solid #E2E8F0;
      padding-top: 3mm;
      display: flex;
      justify-content: space-between;
      font-size: 9.5px;
      color: #64748B;
    }}
  </style>
</head>
<body>
  <div class="stripe"></div>
  <div class="body-wrap">
    <div class="header">
      <div class="brand">
        {'<img src="data:image/png;base64,' + logo_b64 + '" class="logo">' if logo_b64 else ''}
        <div>
          <div style="font-size:16px; font-weight:800; color:{cfg['primary_color']};">{cfg['org_name']}</div>
          <div style="font-size:11px; color:#64748B; font-weight:600;">{cfg['project_name']}</div>
        </div>
      </div>
      <div style="font-size:11px; font-weight:700; color:{cfg['accent_color']};">Partner: {cfg['partner_name']}</div>
    </div>

    <div class="hero">
      <div class="eyebrow">{cfg['eyebrow']}</div>
      <h1 class="title">{cfg['headline']}</h1>
      <p class="subhead">{cfg['subhead']}</p>
    </div>

    <div class="grid-2x2">
      <div class="card">
        <div class="card-title">📜 Official Recommendation Letter</div>
        <div class="card-desc">Signed credential from leadership to boost academic and employment applications.</div>
      </div>
      <div class="card">
        <div class="card-title">🚀 Future Compensated Projects</div>
        <div class="card-desc">Volunteering gives priority selection in future funded speech and AI data collections.</div>
      </div>
      <div class="card">
        <div class="card-title">💻 100% Offline Transcriber</div>
        <div class="card-desc">Zero internet needed during work. Transcribe at your own pace locally on any laptop.</div>
      </div>
      <div class="card">
        <div class="card-title">💬 WhatsApp Community Support</div>
        <div class="card-desc">Direct technical guidance, live assistance, and coordination with fellow volunteers.</div>
      </div>
    </div>

    <div class="cta-box">
      <div>
        <div style="font-size:10px; font-weight:800; color:#E2A000; text-transform:uppercase;">Sign Up in 10 Seconds</div>
        <div style="font-size:17px; font-weight:800; margin:4px 0;">Scan to Get Your Audio Package</div>
        <div style="font-size:12px; opacity:0.9;">Visit the portal to receive your assigned workspace:</div>
        <div class="url-pill">{cfg['short_url']}</div>
      </div>
      <div class="qr-wrap">
        {'<img src="data:image/png;base64,' + qr_b64 + '" class="qr-img">' if qr_b64 else ''}
        <div style="color:#07254B; font-size:8px; font-weight:800; margin-top:2px;">SCAN TO REGISTER</div>
      </div>
    </div>

    <div class="footer">
      <div><b>{cfg['project_name']}</b> | {cfg['org_name']}</div>
      <div>Questions & Submissions: <b>{cfg['contact_email']}</b></div>
    </div>
  </div>
</body>
</html>
"""
    html_out = output_dir / "poster.html"
    with open(html_out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated: {html_out}")

    # Render PDF & PNG via Chrome if available
    try:
        pdf_out = output_dir / "poster.pdf"
        subprocess.run(["google-chrome", "--headless=new", "--disable-gpu", f"--print-to-pdf={pdf_out}", "--no-margins", str(html_out)], capture_output=True, check=True)
        print(f"Rendered: {pdf_out}")
    except Exception as e:
        print(f"Chrome PDF note: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Render recruitment poster from config")
    parser.add_argument("--config", default=None, help="JSON config file")
    parser.add_argument("--outdir", default=".", help="Output directory")
    args = parser.parse_args()

    custom_cfg = {}
    if args.config and Path(args.config).exists():
        with open(args.config) as f:
            custom_cfg = json.load(f)

    render_poster(custom_cfg, Path(args.outdir))
