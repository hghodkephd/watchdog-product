#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alarm UI components for Streamlit dashboard.

UPDATED: Now reads from SQLite-backed persistent alarm store.
The dashboard is READ-ONLY for alarm detection - it just displays what the
background daemon has determined. Silencing/unsilencing writes to DB.
"""

import streamlit as st
import time
from datetime import datetime
from typing import Optional

from storage import get_db_path
from alert_engine import PersistentAlarmStore, Severity, AlarmRecord
from notifications import SUGGESTED_ACTIONS, send_test_email
from config import EmailConfig


def get_alarm_store() -> PersistentAlarmStore:
    """Get the alarm store connected to the main database."""
    return PersistentAlarmStore(get_db_path())


# =============================================================================
# 1. ALARM BANNER COMPONENT
#    Call this at the TOP of tab1 (Monitoring), before other content
# =============================================================================

def render_alarm_banners(cfg):
    """
    Render persistent alarm banners at top of Monitoring tab.
    
    Reads from SQLite-backed alarm store (populated by daemon's AlertEngine).
    Shows all visible alarms with silence controls.
    """
    store = get_alarm_store()
    
    # Get alarms that should be displayed
    # (active and not silenced, or escalated past silence level)
    visible_alarms = store.get_all_visible()
    
    if not visible_alarms:
        return
    
    # Sort: critical first, then by duration
    visible_alarms.sort(key=lambda a: (-a.severity.value, -a.duration_seconds))
    
    st.markdown("### ⚠️ Active Alerts")
    
    for alarm in visible_alarms:
        _render_single_alarm_banner(alarm, store)
    
    st.divider()


def _render_single_alarm_banner(alarm: AlarmRecord, store: PersistentAlarmStore):
    """Render a single alarm banner with controls."""
    # Determine styling
    if alarm.severity == Severity.CRITICAL:
        icon = "🔴"
        color = "#dc2626"
        bg_color = "#fee2e2"
        label = "CRITICAL"
    else:
        icon = "🟡"
        color = "#d97706"
        bg_color = "#fef3c7"
        label = "WARNING"
    
    # Format duration
    dur_sec = alarm.duration_seconds
    if dur_sec >= 3600:
        dur_str = f"{int(dur_sec // 3600)}h {int((dur_sec % 3600) // 60)}m"
    elif dur_sec >= 60:
        dur_str = f"{int(dur_sec // 60)}m"
    else:
        dur_str = f"{int(dur_sec)}s"
    
    # Get suggested action
    action = SUGGESTED_ACTIONS.get(alarm.alert_type, "Check the sensor.")
    
    # Render banner
    with st.container():
        st.markdown(
            f"""
            <div style="
                background: {bg_color};
                border-left: 4px solid {color};
                padding: 12px 16px;
                border-radius: 4px;
                margin-bottom: 8px;
            ">
                <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                    <div>
                        <strong style="color: {color};">{icon} {label}</strong>
                        <span style="color: #666; font-size: 12px; margin-left: 8px;">
                            Active for {dur_str}
                        </span>
                        <br>
                        <strong>{alarm.sensor_name}</strong>: {alarm.message}
                        <br>
                        <span style="color: #666; font-size: 13px;">
                            💡 {action}
                        </span>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
        
        # Silence controls
        if not alarm.is_silenced:
            col1, col2, col3, col4 = st.columns([1, 1, 1, 2])
            with col1:
                if st.button("🔕 15m", key=f"silence_15m_{alarm.alarm_key}", use_container_width=True):
                    store.silence_alarm(alarm.sensor_id, alarm.alert_type, 15 * 60)
                    st.rerun()
            with col2:
                if st.button("🔕 1h", key=f"silence_1h_{alarm.alarm_key}", use_container_width=True):
                    store.silence_alarm(alarm.sensor_id, alarm.alert_type, 60 * 60)
                    st.rerun()
            with col3:
                if st.button("🔕 Until clear", key=f"silence_clear_{alarm.alarm_key}", use_container_width=True):
                    store.silence_alarm(alarm.sensor_id, alarm.alert_type, float('inf'))
                    st.rerun()
        else:
            # Show silenced status with unsilence option
            silence_info = "Silenced"
            if alarm.silenced_until != float('inf'):
                remaining = int(alarm.silenced_until - time.time())
                if remaining > 0:
                    if remaining >= 3600:
                        silence_info = f"Silenced ({remaining // 3600}h {(remaining % 3600) // 60}m left)"
                    else:
                        silence_info = f"Silenced ({remaining // 60}m left)"
            else:
                silence_info = "Silenced until resolved"
            
            col1, col2 = st.columns([2, 1])
            with col1:
                st.caption(f"🔇 {silence_info}")
            with col2:
                if st.button("🔔 Unsilence", key=f"unsilence_{alarm.alarm_key}", use_container_width=True):
                    store.unsilence_alarm(alarm.sensor_id, alarm.alert_type)
                    st.rerun()


# =============================================================================
# 2. ALERT SUMMARY (for use elsewhere in dashboard)
# =============================================================================

def get_alarm_counts() -> dict:
    """Get summary counts of alarms for display."""
    store = get_alarm_store()
    active = store.get_all_active()
    
    return {
        "total": len(active),
        "critical": sum(1 for a in active if a.severity == Severity.CRITICAL),
        "warning": sum(1 for a in active if a.severity == Severity.WARNING),
        "silenced": sum(1 for a in active if a.is_silenced),
    }


def render_alarm_summary_badge():
    """Render a compact alarm summary badge."""
    counts = get_alarm_counts()
    
    if counts["total"] == 0:
        st.success("✅ No active alerts")
    elif counts["critical"] > 0:
        st.error(f"🔴 {counts['critical']} critical, {counts['warning']} warning")
    else:
        st.warning(f"🟡 {counts['warning']} warning alert(s)")

def get_email_circuit_breaker_status(db_path=None) -> dict:
    """Get email circuit breaker status for UI display."""
    if db_path is None:
        db_path = get_db_path()
    
    try:
        from alert_engine import EmailCircuitBreaker
        breaker = EmailCircuitBreaker(db_path)
        state = breaker.get_state()
        allowed, reason = breaker.is_email_allowed()
        
        return {
            "allowed": allowed,
            "reason": reason,
            "consecutive_failures": state.get("consecutive_failures", 0),
            "is_disabled": state.get("disabled_at_ts") is not None,
            "last_error": state.get("last_error_message"),
        }
    except Exception as e:
        return {
            "allowed": True,
            "reason": "",
            "consecutive_failures": 0,
            "is_disabled": False,
            "last_error": None,
            "error": str(e),
        }


def reset_email_circuit_breaker(db_path=None) -> bool:
    """Reset email circuit breaker (called when user re-tests email)."""
    if db_path is None:
        db_path = get_db_path()
    
    try:
        from alert_engine import EmailCircuitBreaker
        breaker = EmailCircuitBreaker(db_path)
        breaker.reset_for_retest()
        return True
    except Exception:
        return False
    
# =============================================================================
# 3. EMAIL SETTINGS UI
#    Add this to the Settings tab (tab3), after alert configuration
# =============================================================================

def render_email_settings(email_config: Optional[EmailConfig]) -> Optional[EmailConfig]:
    """
    Render email notification settings UI.
    
    Returns: updated EmailConfig or None if unchanged
    """
    st.subheader("📧 Email Notifications")
    
    # Check circuit breaker status
    cb_status = get_email_circuit_breaker_status()
    
    if cb_status.get("is_disabled"):
        st.error(
            "⚠️ **Email notifications are paused** due to repeated delivery failures.\n\n"
            f"Last error: {cb_status.get('last_error', 'Unknown')}\n\n"
            "Please check your settings below and click **Send Test Email** to re-enable."
        )
    elif cb_status.get("consecutive_failures", 0) > 0:
        st.warning(
            f"⚠️ Email has failed {cb_status['consecutive_failures']} time(s) recently. "
            "Notifications will retry automatically with increasing delays."
        )
    
    st.info(
        "Get email alerts when sensors exceed thresholds. "
        "Notifications are sent by the background monitoring service, "
        "**even when the dashboard is closed**."
    )
    
    # Use provided config or create default
    if email_config is None:
        email_config = EmailConfig()
    
    # Enable toggle
    enabled = st.checkbox(
        "Enable Email Notifications",
        value=email_config.enabled,
        key="email_enabled"
    )
    
    if not enabled:
        if email_config.enabled:
            # User disabled - return updated config
            return EmailConfig(enabled=False)
        return None
    
    # Email settings form
    col1, col2 = st.columns(2)
    
    with col1:
        recipient = st.text_input(
            "Send alerts to",
            value=email_config.recipient,
            placeholder="your@email.com",
            key="email_recipient"
        )
        
        smtp_server = st.text_input(
            "SMTP Server",
            value=email_config.smtp_server,
            key="email_smtp_server"
        )
        
        sender_email = st.text_input(
            "Sender Email",
            value=email_config.sender_email,
            placeholder="watchdog@gmail.com",
            key="email_sender"
        )
    
    with col2:
        rate_limit = st.slider(
            "Rate limit (minutes between emails)",
            min_value=5,
            max_value=120,
            value=email_config.rate_limit_minutes,
            step=5,
            key="email_rate_limit"
        )
        
        smtp_port = st.number_input(
            "SMTP Port",
            value=email_config.smtp_port,
            min_value=1,
            max_value=65535,
            key="email_smtp_port"
        )
        
        # Password handling with keyring
        current_password_display = "••••••••" if email_config.get_actual_password() else ""
        new_password = st.text_input(
            "App Password",
            value="",
            type="password",
            placeholder=current_password_display or "Enter Gmail app password",
            help="For Gmail, use an App Password (not your regular password). Leave blank to keep existing.",
            key="email_password"
        )
        
        # Only update password if user entered something new
        if new_password:
            email_config.set_password(new_password)
    
    notify_on_clear = st.checkbox(
        "Send email when alarm clears",
        value=email_config.notify_on_clear,
        key="email_notify_clear"
    )
    
    # Test email button
    st.markdown("---")
    
    # For test_config, we need to handle the password correctly:
    # - If user entered a new password, use that for testing
    # - Otherwise, use the existing marker so get_actual_password() retrieves from keyring
    if new_password:
        test_password = new_password
    else:
        test_password = email_config.sender_password  # Will be "__KEYRING__" if migrated
    
    test_config = EmailConfig(
        enabled=True,
        recipient=recipient,
        smtp_server=smtp_server,
        smtp_port=int(smtp_port),
        sender_email=sender_email,
        sender_password=test_password,
        rate_limit_minutes=rate_limit,
        notify_on_clear=notify_on_clear,
    )
    
    # Determine if we have a usable password (new entry or existing in keyring)
    has_password = bool(new_password) or bool(email_config.get_actual_password())
    
    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("📤 Send Test Email", disabled=not test_config.is_configured()):
            # Reset circuit breaker before test
            reset_email_circuit_breaker()
            
            with st.spinner("Sending..."):
                success, msg = send_test_email(test_config)
                if success:
                    st.success("✓ Test email sent! Check your inbox. Email notifications are now enabled.")
                else:
                    st.error(f"✗ Failed: {msg}")
    
    with col2:
        if not test_config.is_configured():
            missing = []
            if not recipient:
                missing.append("recipient")
            if not sender_email:
                missing.append("sender email")
            if not has_password:
                missing.append("password")
            st.caption(f"Missing: {', '.join(missing)}")
    
    # Gmail setup help
    with st.expander("Gmail Setup Help"):
        st.markdown("""
        **To use Gmail for notifications:**
        
        1. Go to [Google Account Security](https://myaccount.google.com/security)
        2. Enable 2-Step Verification (required)
        3. Go to **App passwords**
        4. Generate a new app password for "Mail"
        5. Use that 16-character password here (not your Gmail password)
        
        **SMTP Settings for Gmail:**
        - Server: `smtp.gmail.com`
        - Port: `587`
        """)
    
    # Return updated config
    return test_config

def render_email_system_warning():
    """
    Render a NON-DISMISSABLE warning if email notifications are broken.
    
    This should be called at the TOP of the Monitoring tab, even before alarm banners,
    because email failures are critical for the product's core value proposition.
    """
    cb_status = get_email_circuit_breaker_status()
    
    if cb_status.get("is_disabled"):
        st.error(
            "🚨 **EMAIL NOTIFICATIONS ARE DISABLED**\n\n"
            f"After {cb_status.get('consecutive_failures', 5)} consecutive failures, "
            "email alerts have been paused to prevent spam.\n\n"
            f"**Last error:** {cb_status.get('last_error', 'Unknown')}\n\n"
            "⚠️ **You will NOT receive email alerts until this is fixed.**\n\n"
            "→ Go to **Settings → Email Notifications** to fix and re-test your email settings."
        )
        return True  # Indicates warning was shown
    
    elif cb_status.get("consecutive_failures", 0) >= 2:
        # Show warning after 2+ failures (before full disable at 5)
        failures = cb_status.get("consecutive_failures", 0)
        st.warning(
            f"⚠️ **Email delivery issues detected** ({failures}/5 failures)\n\n"
            "Email notifications may not be working reliably. "
            "Check your email settings if you don't receive alerts.\n\n"
            f"Last error: {cb_status.get('last_error', 'Unknown')}"
        )
        return True
    
    return False  # No warning needed

# =============================================================================
# 4. NOTIFICATION LOG VIEWER (optional, for debugging)
# =============================================================================

def render_notification_log(limit: int = 20):
    """Render recent notification log for debugging."""
    import sqlite3
    from storage import get_db_path
    
    st.subheader("📬 Recent Notifications")
    
    try:
        conn = sqlite3.connect(get_db_path(), timeout=5.0)
        conn.row_factory = sqlite3.Row
        
        rows = conn.execute("""
            SELECT alarm_key, notification_type, sent_ts, success, error_message
            FROM alert_notifications
            ORDER BY sent_ts DESC
            LIMIT ?
        """, (limit,)).fetchall()
        
        conn.close()
        
        if not rows:
            st.caption("No notifications sent yet.")
            return
        
        for row in rows:
            ts = datetime.fromtimestamp(row['sent_ts']).strftime("%Y-%m-%d %H:%M:%S")
            success = row['success']
            icon = "✅" if success else "❌"
            
            st.text(f"{icon} [{ts}] {row['notification_type']}: {row['alarm_key']}")
            if not success and row['error_message']:
                st.caption(f"   Error: {row['error_message']}")
    
    except Exception as e:
        st.error(f"Could not load notification log: {e}")


# =============================================================================
# DEPRECATED: process_alerts_and_notify
# =============================================================================

def process_alerts_and_notify(alerts, cfg, email_config):
    """
    DEPRECATED: Alert processing now happens in the background daemon.
    
    This function is kept for backward compatibility but does nothing.
    The AlertEngine in ble_watchdog.py handles all alert processing and
    notification sending 24/7, regardless of dashboard state.
    """
    # No-op - alerts are processed by the daemon's AlertEngine
    pass
