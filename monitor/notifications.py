#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Email notifications for Watchdog alarms.

Simple SMTP-based email sending with rate limiting.
"""

from __future__ import annotations

import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import TYPE_CHECKING

from logging_config import get_logger

# Import EmailConfig from config to avoid duplicate definitions
if TYPE_CHECKING:
    from config import EmailConfig

_log = get_logger("watchdog.email")


# Suggested actions per alert type
SUGGESTED_ACTIONS = {
    "temp_high": "Check cooling or ventilation. Move sensor away from heat sources.",
    "temp_low": "Check heating system. Ensure area is properly insulated.",
    "low_battery": "Replace sensor battery soon to avoid data gaps.",
    "offline": "Check sensor placement and signal strength. May need new battery.",
    "low_disk": "Archive old data or free disk space on the Pi.",
}


def _send_email(config: "EmailConfig", subject: str, body_text: str, body_html: str) -> tuple[bool, str]:
    """Send email via SMTP. Returns (success, message)."""
    if not config.is_configured():
        return False, "Email not configured"
    
    # Check if email is disabled due to errors
    if getattr(config, 'email_disabled_due_to_errors', False):
        return False, "Email disabled due to repeated failures. Please re-test in Settings."
    
    # Get actual password from keyring
    actual_password = config.get_actual_password()
    if not actual_password:
        return False, "Email password not found. Please re-enter in Settings."
    
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = config.sender_email
        msg["To"] = config.recipient
        msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))
        
        context = ssl.create_default_context()
        with smtplib.SMTP(config.smtp_server, config.smtp_port, timeout=30) as server:
            server.starttls(context=context)
            server.login(config.sender_email, actual_password)
            server.sendmail(config.sender_email, config.recipient, msg.as_string())
        
        _log.info("Email sent: %s → %s", subject, config.recipient)
        return True, "Sent"
    
    except smtplib.SMTPAuthenticationError:
        _log.error("SMTP auth failed")
        return False, "Email login failed. Check your email address and app password."
    except smtplib.SMTPRecipientsRefused:
        _log.error("SMTP recipient refused")
        return False, "Recipient email address was rejected. Check the address."
    except smtplib.SMTPServerDisconnected:
        _log.error("SMTP server disconnected")
        return False, "Email server disconnected. Check your internet connection."
    except smtplib.SMTPException as e:
        _log.error("SMTP error: %s", e)
        return False, f"Could not send email. Server said: {str(e)[:100]}"
    except TimeoutError:
        _log.error("SMTP timeout")
        return False, "Email server did not respond. Check your internet connection."
    except Exception as e:
        _log.exception("Email failed")
        return False, f"Unexpected error: {str(e)[:100]}"


def send_test_email(config: "EmailConfig") -> tuple[bool, str]:
    """Send test email to verify configuration."""
    subject = "🐕 Watchdog Test Email"
    body_text = (
        "This is a test email from Watchdog Environmental Monitor.\n\n"
        "If you received this, email notifications are configured correctly!\n\n"
        "---\nWatchdog Environmental Monitor"
    )
    body_html = """
<html><body style="font-family: system-ui, sans-serif; padding: 20px;">
<h2>🐕 Watchdog Test Email</h2>
<p>This is a test email from Watchdog Environmental Monitor.</p>
<p style="color: green; font-weight: bold;">✓ Email notifications are configured correctly!</p>
<hr><p style="color: #666; font-size: 12px;">Watchdog Environmental Monitor</p>
</body></html>
"""
    return _send_email(config, subject, body_text, body_html)


def send_alarm_email(
    config: "EmailConfig",
    sensor_name: str,
    sensor_id: str,
    severity: str,
    alert_type: str,
    message: str,
    current_value: str = None,
    threshold: str = None,
) -> tuple[bool, str]:
    """Send alarm notification email."""
    if not config.is_configured():
        return False, "Email not configured"
    
    icon = "🔴" if severity == "critical" else "🟡"
    label = "CRITICAL" if severity == "critical" else "WARNING"
    action = SUGGESTED_ACTIONS.get(alert_type, "Check the sensor and environment.")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    subject = f"{icon} Watchdog {label}: {sensor_name}"
    
    # Plain text
    lines = [
        f"{icon} {label} ALERT",
        "",
        f"Sensor: {sensor_name} ({sensor_id})",
        f"Alert: {message}",
    ]
    if current_value:
        lines.append(f"Current: {current_value}")
    if threshold:
        lines.append(f"Threshold: {threshold}")
    lines.extend([
        "",
        f"Action: {action}",
        "",
        f"Time: {now_str}",
        "",
        "---",
        "Watchdog Environmental Monitor",
    ])
    body_text = "\n".join(lines)
    
    # HTML
    bg = "#fee2e2" if severity == "critical" else "#fef3c7"
    border = "#dc2626" if severity == "critical" else "#d97706"
    
    value_row = f'<tr><td style="padding:6px 0;color:#666">Current:</td><td>{current_value}</td></tr>' if current_value else ""
    thresh_row = f'<tr><td style="padding:6px 0;color:#666">Threshold:</td><td>{threshold}</td></tr>' if threshold else ""
    
    body_html = f"""
<html><body style="font-family: system-ui, sans-serif; padding: 20px;">
<div style="background:{bg}; border-left:4px solid {border}; padding:20px; border-radius:8px; max-width:500px;">
<h2 style="margin-top:0">{icon} {label} ALERT</h2>
<table style="width:100%">
<tr><td style="padding:6px 0;color:#666">Sensor:</td><td style="font-weight:bold">{sensor_name}</td></tr>
<tr><td style="padding:6px 0;color:#666">Alert:</td><td>{message}</td></tr>
{value_row}
{thresh_row}
</table>
<div style="background:white; padding:12px; border-radius:4px; margin-top:16px">
<strong>Suggested Action:</strong><br>{action}
</div>
<p style="color:#666; font-size:12px; margin-bottom:0">{now_str}</p>
</div>
<p style="color:#666; font-size:12px; margin-top:20px">
Open your Watchdog dashboard to view details and manage alerts.
</p>
</body></html>
"""
    return _send_email(config, subject, body_text, body_html)


def send_cleared_email(
    config: "EmailConfig",
    sensor_name: str,
    sensor_id: str,
    alert_type: str,
    duration_minutes: int,
) -> tuple[bool, str]:
    """Send notification that alarm cleared. Only for alarms > 5 min."""
    if not config.is_configured():
        return False, "Email not configured"
    if duration_minutes < 5:
        return True, "Skipped (short duration)"
    
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    dur_str = f"{duration_minutes // 60}h {duration_minutes % 60}m" if duration_minutes >= 60 else f"{duration_minutes}m"
    
    subject = f"✅ Watchdog: {sensor_name} alarm cleared"
    
    body_text = (
        f"✅ ALARM CLEARED\n\n"
        f"Sensor: {sensor_name} ({sensor_id})\n"
        f"Duration: {dur_str}\n\n"
        f"The reading has returned to normal.\n\n"
        f"Time: {now_str}\n\n"
        f"---\nWatchdog Environmental Monitor"
    )
    
    body_html = f"""
<html><body style="font-family: system-ui, sans-serif; padding: 20px;">
<div style="background:#d1fae5; border-left:4px solid #059669; padding:20px; border-radius:8px; max-width:500px;">
<h2 style="margin-top:0">✅ Alarm Cleared</h2>
<table style="width:100%">
<tr><td style="padding:6px 0;color:#666">Sensor:</td><td style="font-weight:bold">{sensor_name}</td></tr>
<tr><td style="padding:6px 0;color:#666">Duration:</td><td>{dur_str}</td></tr>
</table>
<p style="color:#059669; font-weight:bold; margin-top:16px">The reading has returned to normal.</p>
<p style="color:#666; font-size:12px; margin-bottom:0">{now_str}</p>
</div>
</body></html>
"""
    return _send_email(config, subject, body_text, body_html)
