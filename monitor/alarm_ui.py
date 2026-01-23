#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alarm UI components for Streamlit dashboard.

ADD these functions to app.py and call them from the appropriate places.
"""

import streamlit as st
import time
from datetime import datetime

# =============================================================================
# IMPORTS TO ADD at top of app.py
# =============================================================================
# from alarm_state import get_alarm_manager, process_alerts, Severity, SILENCE_OPTIONS
# from notifications import EmailConfig, send_test_email, send_alarm_email, send_cleared_email


# =============================================================================
# 1. ALARM BANNER COMPONENT
#    Call this at the TOP of tab1 (Monitoring), before other content
# =============================================================================

def render_alarm_banners(cfg):
    """
    Render persistent alarm banners at top of Monitoring tab.
    
    Shows all visible alarms with silence controls.
    """
    manager = get_alarm_manager()
    visible_alarms = manager.get_all_visible()
    
    if not visible_alarms:
        return
    
    # Sort: critical first, then by duration
    visible_alarms.sort(key=lambda a: (-a.severity.value, -a.duration_seconds))
    
    st.markdown("### ⚠️ Active Alerts")
    
    for alarm in visible_alarms:
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
        from notifications import SUGGESTED_ACTIONS
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
                    if st.button("🔕 15m", key=f"silence_15m_{alarm.key}", use_container_width=True):
                        manager.silence(alarm.sensor_id, alarm.alert_type, "15min")
                        st.rerun()
                with col2:
                    if st.button("🔕 1h", key=f"silence_1h_{alarm.key}", use_container_width=True):
                        manager.silence(alarm.sensor_id, alarm.alert_type, "1hour")
                        st.rerun()
                with col3:
                    if st.button("🔕 Until clear", key=f"silence_clear_{alarm.key}", use_container_width=True):
                        manager.silence(alarm.sensor_id, alarm.alert_type, "until_resolved")
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
                    if st.button("🔔 Unsilence", key=f"unsilence_{alarm.key}", use_container_width=True):
                        manager.unsilence(alarm.sensor_id, alarm.alert_type)
                        st.rerun()
    
    st.divider()


# =============================================================================
# 2. PROCESS ALERTS AND SEND NOTIFICATIONS
#    Call this after check_alerts() in the Monitoring tab
# =============================================================================

def process_alerts_and_notify(alerts, cfg, email_config):
    """
    Process alerts through alarm state manager and send notifications.
    
    Call this after check_alerts() returns alerts:
    
        alerts = check_alerts(df_latest, cfg)
        process_alerts_and_notify(alerts, cfg, email_config)
    """
    from alarm_state import process_alerts, get_alarm_manager
    from notifications import send_alarm_email, send_cleared_email
    
    manager = get_alarm_manager()
    
    # Process alerts into alarm states
    updated, new_alarms, escalated = process_alerts(alerts)
    
    # Send email notifications if configured
    if email_config and email_config.is_configured():
        rate_limit_sec = email_config.rate_limit_minutes * 60
        
        # Notify on new alarms
        for alarm in new_alarms:
            if manager.should_notify(alarm.sensor_id, alarm.alert_type, rate_limit_sec):
                success, msg = send_alarm_email(
                    config=email_config,
                    sensor_name=alarm.sensor_name,
                    sensor_id=alarm.sensor_id,
                    severity=alarm.severity.name.lower(),
                    alert_type=alarm.alert_type,
                    message=alarm.message,
                )
                if success:
                    manager.mark_notified(alarm.sensor_id, alarm.alert_type)
        
        # Notify on escalations
        for alarm in escalated:
            if manager.should_notify(alarm.sensor_id, alarm.alert_type, rate_limit_sec):
                success, msg = send_alarm_email(
                    config=email_config,
                    sensor_name=alarm.sensor_name,
                    sensor_id=alarm.sensor_id,
                    severity=alarm.severity.name.lower(),
                    alert_type=alarm.alert_type,
                    message=f"ESCALATED: {alarm.message}",
                )
                if success:
                    manager.mark_notified(alarm.sensor_id, alarm.alert_type)
    
    return updated, new_alarms, escalated


# =============================================================================
# 3. EMAIL SETTINGS UI
#    Add this to the Settings tab (tab3), after alert configuration
# =============================================================================

def render_email_settings(email_config):
    """
    Render email notification settings UI.
    
    Returns: updated EmailConfig or None if unchanged
    """
    st.subheader("📧 Email Notifications")
    
    st.info(
        "Get email alerts when sensors exceed thresholds. "
        "Emails are rate-limited to prevent spam."
    )
    
    # Enable toggle
    enabled = st.checkbox(
        "Enable Email Notifications",
        value=email_config.enabled if email_config else False,
        key="email_enabled"
    )
    
    if not enabled:
        if email_config and email_config.enabled:
            # User disabled - return updated config
            return EmailConfig(enabled=False)
        return None
    
    # Email settings form
    col1, col2 = st.columns(2)
    
    with col1:
        recipient = st.text_input(
            "Send alerts to",
            value=email_config.recipient if email_config else "",
            placeholder="your@email.com",
            key="email_recipient"
        )
        
        smtp_server = st.text_input(
            "SMTP Server",
            value=email_config.smtp_server if email_config else "smtp.gmail.com",
            key="email_smtp_server"
        )
        
        sender_email = st.text_input(
            "Sender Email",
            value=email_config.sender_email if email_config else "",
            placeholder="watchdog@gmail.com",
            key="email_sender"
        )
    
    with col2:
        rate_limit = st.slider(
            "Rate limit (minutes between emails)",
            min_value=5,
            max_value=120,
            value=email_config.rate_limit_minutes if email_config else 30,
            step=5,
            key="email_rate_limit"
        )
        
        smtp_port = st.number_input(
            "SMTP Port",
            value=email_config.smtp_port if email_config else 587,
            min_value=1,
            max_value=65535,
            key="email_smtp_port"
        )
        
        sender_password = st.text_input(
            "App Password",
            value=email_config.sender_password if email_config else "",
            type="password",
            help="For Gmail, use an App Password (not your regular password)",
            key="email_password"
        )
    
    notify_on_clear = st.checkbox(
        "Send email when alarm clears",
        value=email_config.notify_on_clear if email_config else True,
        key="email_notify_clear"
    )
    
    # Test email button
    st.markdown("---")
    
    test_config = EmailConfig(
        enabled=True,
        recipient=recipient,
        smtp_server=smtp_server,
        smtp_port=int(smtp_port),
        sender_email=sender_email,
        sender_password=sender_password,
        rate_limit_minutes=rate_limit,
        notify_on_clear=notify_on_clear,
    )
    
    col1, col2 = st.columns([1, 3])
    with col1:
        if st.button("📤 Send Test Email", disabled=not test_config.is_configured()):
            with st.spinner("Sending..."):
                from notifications import send_test_email
                success, msg = send_test_email(test_config)
                if success:
                    st.success("✓ Test email sent! Check your inbox.")
                else:
                    st.error(f"✗ Failed: {msg}")
    
    with col2:
        if not test_config.is_configured():
            missing = []
            if not recipient:
                missing.append("recipient")
            if not sender_email:
                missing.append("sender email")
            if not sender_password:
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


# =============================================================================
# 4. INTEGRATION EXAMPLE - How to modify app.py
# =============================================================================

"""
In app.py, make these changes:

1. ADD IMPORTS at top:
   from alarm_state import get_alarm_manager, process_alerts, Severity, SILENCE_OPTIONS
   from notifications import EmailConfig, send_test_email, send_alarm_email

2. LOAD EMAIL CONFIG (after cfg = load_config()):
   email_config = getattr(cfg, 'email', None) or EmailConfig()

3. IN TAB 1 (Monitoring), BEFORE other content:
   # Render alarm banners at top
   render_alarm_banners(cfg)

4. IN TAB 1, AFTER check_alerts():
   # Replace:
   #   alerts = check_alerts(df_latest, cfg)
   # With:
   alerts = check_alerts(df_latest, cfg)
   process_alerts_and_notify(alerts, cfg, email_config)

5. IN TAB 3 (Settings), AFTER alert config section:
   st.divider()
   new_email_config = render_email_settings(email_config)
   if new_email_config:
       cfg.email = new_email_config
       # Save will happen with "Save All Settings" button
"""


# =============================================================================
# 5. QUICK INTEGRATION - Copy-paste block for top of Monitoring tab
# =============================================================================

def alarm_banner_block(cfg):
    """
    Quick integration block. Call at top of Monitoring tab.
    
    Usage in app.py:
        with tab1:
            from alarm_ui import alarm_banner_block
            alarm_banner_block(cfg)
            # ... rest of monitoring tab ...
    """
    try:
        from alarm_state import get_alarm_manager, Severity
        from notifications import SUGGESTED_ACTIONS
        
        manager = get_alarm_manager()
        visible = manager.get_all_visible()
        
        if not visible:
            return
        
        visible.sort(key=lambda a: (-a.severity.value, -a.duration_seconds))
        
        for alarm in visible:
            is_crit = alarm.severity == Severity.CRITICAL
            icon = "🔴" if is_crit else "🟡"
            label = "CRITICAL" if is_crit else "WARNING"
            
            dur = alarm.duration_seconds
            dur_str = f"{int(dur//3600)}h {int((dur%3600)//60)}m" if dur >= 3600 else f"{int(dur//60)}m" if dur >= 60 else f"{int(dur)}s"
            
            action = SUGGESTED_ACTIONS.get(alarm.alert_type, "Check the sensor.")
            
            if is_crit:
                st.error(f"{icon} **{label}** ({dur_str}): **{alarm.sensor_name}** - {alarm.message}\n\n💡 {action}")
            else:
                st.warning(f"{icon} **{label}** ({dur_str}): **{alarm.sensor_name}** - {alarm.message}\n\n💡 {action}")
            
            if not alarm.is_silenced:
                c1, c2, c3, _ = st.columns([1, 1, 1, 2])
                if c1.button("🔕 15m", key=f"s15_{alarm.key}"):
                    manager.silence(alarm.sensor_id, alarm.alert_type, "15min")
                    st.rerun()
                if c2.button("🔕 1h", key=f"s1h_{alarm.key}"):
                    manager.silence(alarm.sensor_id, alarm.alert_type, "1hour")
                    st.rerun()
                if c3.button("🔕 Clear", key=f"scl_{alarm.key}"):
                    manager.silence(alarm.sensor_id, alarm.alert_type, "until_resolved")
                    st.rerun()
            else:
                if st.button("🔔 Unsilence", key=f"uns_{alarm.key}"):
                    manager.unsilence(alarm.sensor_id, alarm.alert_type)
                    st.rerun()
        
        st.divider()
    
    except ImportError:
        pass  # Alarm modules not yet installed
