from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .config import Config, RuntimeConfig
from .state import write_json


def notification_summary(config: Config) -> dict[str, Any]:
    source = config.vault / "00 Command Center/Needs Attention.md"
    lines = source.read_text(encoding="utf-8").splitlines() if source.exists() else []
    items = [line[2:] for line in lines if line.startswith("- ") and not line.startswith("- _")]
    title = "Andy Brain review"
    if items:
        body = f"{len(items)} item(s) need attention. {items[0][:180]}"
    else:
        body = "Your Command Center has no flagged items. Open Claude to review current work."
    return {"title": title, "body": body, "items": items, "command_center": str(config.vault / "00 Command Center/Home.md")}


def notification_script(config: Config) -> Path:
    script = config.engine / "scripts/windows/Show-AndyBrainNotification.ps1"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(
        """param(
  [Parameter(Mandatory=$true)][string]$Title,
  [Parameter(Mandatory=$true)][string]$Body
)
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).AppendChild($xml.CreateTextNode($Title)) | Out-Null
$texts.Item(1).AppendChild($xml.CreateTextNode($Body)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Andy Brain').Show($toast)
""",
        encoding="utf-8",
    )
    return script


def review_task_script(config: Config, runtime: RuntimeConfig) -> Path:
    script = config.engine / "scripts/windows/Run-AndyBrainReview.ps1"
    script.parent.mkdir(parents=True, exist_ok=True)
    python = runtime.python3_executable or "python"
    script.write_text(
        f"""$ErrorActionPreference = 'Stop'
& '{python}' '{config.engine / 'brain'}' run-once
$summary = & '{python}' '{config.engine / 'brain'}' notifications summary | ConvertFrom-Json
& '{notification_script(config)}' -Title $summary.title -Body $summary.body
""",
        encoding="utf-8",
    )
    return script


def install_schedule(config: Config, runtime: RuntimeConfig, time_of_day: str = "09:00") -> dict[str, Any]:
    task_script = review_task_script(config, runtime)
    task_name = "Andy Brain Daily Review"
    command = [
        "schtasks",
        "/Create",
        "/TN",
        task_name,
        "/TR",
        f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{task_script}"',
        "/SC",
        "DAILY",
        "/ST",
        time_of_day,
        "/F",
    ]
    result: dict[str, Any] = {"task_name": task_name, "script": str(task_script), "time": time_of_day, "command": command, "installed": False}
    if __import__("os").name == "nt":
        process = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        result.update({"installed": process.returncode == 0, "stdout": process.stdout, "stderr": process.stderr, "exit_code": process.returncode})
    else:
        result["reason"] = "Task Scheduler installation must run on Windows. Scripts were generated for the setup wizard."
    write_json(config.engine / "data/state/windows-notification-schedule.json", result)
    return result


def send_notification(config: Config) -> dict[str, Any]:
    summary = notification_summary(config)
    script = notification_script(config)
    result = {**summary, "script": str(script), "sent": False}
    if __import__("os").name == "nt":
        process = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Title", summary["title"], "-Body", summary["body"]], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        result.update({"sent": process.returncode == 0, "stdout": process.stdout, "stderr": process.stderr, "exit_code": process.returncode})
    else:
        result["reason"] = "Native toast delivery must run on Windows."
    return result
