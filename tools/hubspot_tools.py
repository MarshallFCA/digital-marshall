import datetime
import json
import re
import requests
import pandas as pd
import streamlit as st

from tools.core_utils import (
    get_secure_endpoint, 
    sanitize_error_log, 
    call_gemini_api
)

# ==========================================
# TOOL 9: HUBSPOT DISPUTE INTEGRATION
# ==========================================
def sanitize_hubspot_payload(payload_dict: dict) -> dict:
    sanitized = {}
    for key, value in payload_dict.items():
        if pd.isna(value) or value is None:
            sanitized[key] = ""
        else:
            sanitized[key] = str(value)
    return sanitized

def create_hubspot_dispute_ticket(variance_data: dict, service_key: str) -> dict:
    url = get_secure_endpoint("hubspot_tickets", "aHR0cHM6Ly9hcGkuaHViYXBpLmNvbS9jcm0vdjMvb2JqZWN0cy90aWNrZXRz")
    headers = {
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json"
    }
    
    diagnostic_logs = []
    
    connote = variance_data.get("connote", "Unknown Connote")
    variance_amount = variance_data.get("variance_amount", 0.0)
    analysis = variance_data.get("analysis", "No forensic analysis provided.")
    carrier_name = variance_data.get("carrier_name", "Unknown Carrier")
    invoice_number = variance_data.get("invoice_number", "Unknown Invoice")
    
    raw_properties = {
        "hs_pipeline": "0",
        "hs_pipeline_stage": "1",
        "subject": f"Dispute: {carrier_name} - Connote {connote} (Var: ${variance_amount:.2f})",
        "content": f"Automated BOOF Variance Analysis:\n\n{analysis}",
        "carrier_name": carrier_name,
        "variance_amount": variance_amount,
        "invoice_number": invoice_number,
        "dispute_status": "Action Required"
    }
    
    clean_properties = sanitize_hubspot_payload(raw_properties)
    payload = { "properties": clean_properties }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        diagnostic_logs.append(f"HTTP {response.status_code}: POST Hubspot Tickets Endpoint")
        response.raise_for_status()
        
        data = response.json()
        ticket_id = data.get("id")
        diagnostic_logs.append(f"SUCCESS: HubSpot Ticket created. ID: {ticket_id}")
        
        return { "status": "success", "ticket_id": ticket_id, "logs": diagnostic_logs }
        
    except requests.exceptions.RequestException as e:
        diagnostic_logs.append(f"EXCEPTION: {sanitize_error_log(str(e))}")
        if e.response is not None and e.response.text:
            diagnostic_logs.append(f"RESPONSE PAYLOAD: {sanitize_error_log(e.response.text)}")
            
        return { "status": "failed", "ticket_id": None, "logs": diagnostic_logs }

# ==========================================
# TOOL 10 & 11 HUBSPOT HELPER METHODS
# ==========================================
def check_hubspot_duplicate(ms_number: str, service_key: str) -> bool:
    url = get_secure_endpoint("hubspot_search", "aHR0cHM6Ly9hcGkuaHViYXBpLmNvbS9jcm0vdjMvb2JqZWN0cy90aWNrZXRzL3NlYXJjaA==")
    headers = {
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json"
    }
    search_payload = {
        "filterGroups": [{
            "filters": [{
                "propertyName": "subject",
                "operator": "CONTAINS_TOKEN",
                "value": ms_number
            }]
        }]
    }
    try:
        response = requests.post(url, headers=headers, json=search_payload, timeout=15)
        if response.status_code == 200:
            return response.json().get('total', 0) > 0
    except Exception as e:
        print(f"HubSpot Duplicate Check Error ({ms_number}): {sanitize_error_log(str(e))}")
    return False

# ==========================================
# TOOL 10: FREIGHT ALERT AUTOMATOR (MASTER)
# ==========================================
CARRIER_ROUTING_RULES = """
- Hi Trans: Always email customerservice@hi-trans.com.au.
- TNT Express / FedEx Australia: Always email audcc_connect@fedex.com.
- Followmont Transport: Always email customerservice@followmont.com.au.
- Northline Distribution: Always email customer.service@northline.com.au.
- Maitex Pty Ltd: Always email ops@maitex.com.au.
- Sadleirs Logistics: Always email customerservice@sadleirs.com.au.
- VT Freight Express: Always email custserv@vtfe.com.au.
- Hunter EXP: Always email pickupsvic@hunterexpress.com.au.
- Courrio: Always email customersupport@courrio.com.
- Team Global Express: Always email customer.service@teamglobalexp.com.

- Direct Couriers: 
  If delivering to NSW, email customer@directcouriers.com.au. 
  If delivering to VIC, email customer@melb.directcouriers.com.au. 
  If delivering to QLD, email customer@bris.directcouriers.com.au. 
  If delivering to WA, email customer@perth.directcouriers.com.au.
"""

def tool_10_freight_alert_automator(dry_run: bool = False):
    now = datetime.datetime.now()
    offset = 1
    if now.weekday() == 0: 
        offset = 3
    elif now.weekday() == 6: 
        offset = 2
    prev_weekday = (now - datetime.timedelta(days=offset)).date()
    
    def get_next_business_day_10am():
        now_dt = datetime.datetime.now()
        next_dt = now_dt + datetime.timedelta(days=1)
        while next_dt.weekday() >= 5: 
            next_dt += datetime.timedelta(days=1)
        return next_dt.replace(hour=10, minute=0, second=0, microsecond=0).strftime('%Y-%m-%dT%H:%M:%S')
    
    try:
        ms_token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
        hs_key = st.secrets.get("hubspot", {}).get("service_key")
        
        base_url = get_secure_endpoint("machship_recent", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvZ2V0UmVjZW50bHlDcmVhdGVkT3JVcGRhdGVkQ29uc2lnbm1lbnRz")
        headers = { "token": ms_token, "Content-Type": "application/json" }
        
        active_data = []
        
        for i in range(2):
            chunk_to = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=i*7)
            chunk_from = chunk_to - datetime.timedelta(days=7)
            
            params = {
                "fromDateUtc": chunk_from.strftime('%Y-%m-%dT%H:%M:%S'),
                "toDateUtc": chunk_to.strftime('%Y-%m-%dT%H:%M:%S')
            }
            
            resp = requests.get(base_url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                page_data = resp.json().get('object', [])
                if page_data:
                    active_data.extend(page_data)
        
        if not active_data:
            return "Sweep Complete. No active freight found in the designated date range."
            
        pre_pickup_statuses = ['despatched', 'unmanifested', 'printed', 'booked', 'manifested']
        success_statuses = ['delivered', 'on board for delivery', 'partially delivered', 'awaiting collection', 'completed', 'complete']
        error_statuses = ['exception', 'delayed', 'held', 'damaged', 'missed pickup', 'partial']
        
        exceptions = []
        
        def safe_extract_date(date_str):
            if not date_str: return None
            try:
                return datetime.datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
            except:
                return None

        next_biz_10am = get_next_business_day_10am()

        for item in active_data:
            c_id = item.get('consignmentNumber')
            internal_id = item.get('id')
            carrier = item.get('carrier', {}).get('name', 'Unknown Carrier')
            
            track_status = item.get('consignmentTrackingStatus', {}).get('name', '').lower()
            gen_status = item.get('status', {}).get('name', '').lower()
            status_set = {track_status, gen_status}
            
            raw_despatch = item.get('despatchDateLocal') or item.get('despatchDate') or item.get('creationDate')
            raw_eta = item.get('etaLocal') or item.get('eta') or item.get('expectedDeliveryDate')
            
            despatch_date = safe_extract_date(raw_despatch)
            eta_date = safe_extract_date(raw_eta)
            
            to_node = item.get('despatch', {}).get('toLocation', {}) or item.get('toLocation', {})
            suburb = to_node.get('suburb', 'Unknown')
            state = to_node.get('state', 'Unknown')
            postcode = to_node.get('postcode', 'Unknown')
            destination = f"{suburb}, {state} {postcode}"
            
            missed_pickup = False
            missed_delivery = False
            explicit_error = False
            
            if despatch_date and despatch_date <= prev_weekday:
                if any(s in pre_pickup_statuses for s in status_set):
                    missed_pickup = True
                    
            if eta_date and eta_date <= prev_weekday:
                if not any(s in success_statuses for s in status_set) and not missed_pickup:
                    missed_delivery = True
                    
            if any(s in error_statuses for s in status_set):
                explicit_error = True
                
            if missed_pickup:
                reason = "Missed Pickup"
            elif explicit_error:
                reason = f"Carrier Error Status"
            elif missed_delivery:
                reason = "Missed Delivery ETA"
            else:
                continue
                
            exceptions.append({
                "ms_number": c_id,
                "internal_id": internal_id,
                "carrier_name": carrier,
                "destination": destination,
                "status_display": (track_status or gen_status).title(),
                "reason": reason
            })
            
        if not exceptions:
            return "Sweep Complete. No anomalous freight detected."

        routing_prompt = f"""
        You are a highly logical freight routing API. I am giving you a list of plain-text routing rules and a JSON array of freight exceptions containing their carrier and delivery destination.
        
        ROUTING RULES:
        {CARRIER_ROUTING_RULES}
        
        CONSIGNMENTS TO ROUTE:
        {json.dumps(exceptions)}
        
        TASK:
        Evaluate each consignment's 'carrier_name' and 'destination' against the routing rules to deduce the correct email address. 
        If a carrier is not mentioned in the rules, or you cannot deduce an email, set it to "UNMAPPED".
        
        CRITICAL: Return ONLY a valid, raw JSON array of objects with strictly two keys: 'ms_number' and 'routed_email'.
        """
        
        try:
            llm_text = call_gemini_api(routing_prompt, json_mode=True)
            amatch = re.search(r"\[.*\]", llm_text, re.DOTALL | re.IGNORECASE)
            if amatch:
                llm_text = amatch.group(0).strip()
                
            routed_map = json.loads(llm_text)
            
            if isinstance(routed_map, list):
                email_dict = {r.get('ms_number'): r.get('routed_email') for r in routed_map}
            else:
                email_dict = {}
        except Exception as e:
            return f"🚨 CRITICAL CRASH (LLM Routing Engine Failed): {sanitize_error_log(str(e))}"
            
        action_summary = []
        hs_url = get_secure_endpoint("hubspot_tickets", "aHR0cHM6Ly9hcGkuaHViYXBpLmNvbS9jcm0vdjMvb2JqZWN0cy90aWNrZXRz")
        
        for ex in exceptions:
            ms_number = ex['ms_number']
            internal_id = ex.get('internal_id')
            carrier_name = ex['carrier_name']
            destination = ex['destination']
            status_display = ex['status_display']
            reason = ex['reason']
            
            carrier_email = email_dict.get(ms_number, "UNMAPPED")
            action_taken = ""
            
            if dry_run:
                action_taken = f"[DRY RUN SAFE MODE] Would dynamically route email to {carrier_email} and sync to HubSpot."
            else:
                if hs_key:
                    if check_hubspot_duplicate(ms_number, hs_key):
                        action_taken = f"Skipped: HubSpot Ticket already exists for {ms_number}."
                    else:
                        hs_priority = "MEDIUM"
                        
                        if reason == "Missed Pickup":
                            rebook_url = get_secure_endpoint("machship_rebook", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9tYW5pZmVzdHMvcmVib29rUGlja3Vw")
                            rebook_payload = {
                                "consignmentIds": [internal_id],
                                "despatchDateTimeLocal": next_biz_10am
                            }
                            rebook_status = "Failed to autonomously rebook."
                            try:
                                rb_resp = requests.post(rebook_url, headers=headers, json=rebook_payload, timeout=15)
                                if rb_resp.status_code == 200:
                                    rebook_status = f"Autonomously rebooked via API for {next_biz_10am}."
                                else:
                                    rebook_status = f"Rebook API rejected payload (HTTP {rb_resp.status_code})."
                                    hs_priority = "HIGH"
                            except Exception as e:
                                rebook_status = f"Rebook API Crash: {sanitize_error_log(str(e))}"
                                hs_priority = "HIGH"

                            message_text = f"Hello,\n\nConsignment {ms_number} was manifested but missed its pickup. {rebook_status}\n\nPlease ensure collection occurs.\n\nThank you,\nFreight Companies Australia"
                            action_taken = rebook_status
                        else:
                            message_text = f"Hello,\n\nWe are requesting a formal status update on consignment {ms_number}. It is currently showing as '{status_display}' and has been flagged for {reason}.\n\nPlease investigate and provide an updated ETA.\n\nThank you,\nFreight Companies Australia"
                            action_taken = f"Ticket successfully created. Draft routed to {carrier_email}."
                        
                        hs_headers = { "Authorization": f"Bearer {hs_key}", "Content-Type": "application/json" }
                        raw_properties = {
                            "hs_pipeline": "0",  
                            "hs_pipeline_stage": "1",  
                            "subject": f"SERVICE ALERT: {ms_number} ({carrier_name})",
                            "content": f"An autonomous query has flagged a freight anomaly.\n\nConsignment: {ms_number}\nCarrier: {carrier_name}\nDestination: {destination}\nAnomaly Trigger: {reason}\nCurrent Status: {status_display}\n\n=== DRAFT EMAIL TO COPY/PASTE FOR {carrier_email} ===\n{message_text}",
                            "hs_ticket_priority": hs_priority
                        }
                        
                        clean_properties = sanitize_hubspot_payload(raw_properties)
                        try:
                            requests.post(hs_url, headers=hs_headers, json={"properties": clean_properties}, timeout=15)
                        except Exception:
                            action_taken = "Failed to sync to HubSpot."
                else:
                    action_taken = "HubSpot API key missing."
                    
            action_summary.append(f"{ms_number} ({carrier_name} - {reason}): {action_taken}")
            
        return f"Sweep Complete. Processed {len(exceptions)} anomalies.\n" + "\n".join(action_summary)
        
    except Exception as e:
        return f"🚨 CRITICAL CRASH: {sanitize_error_log(str(e))}"

# ==========================================
# TOOL 16: WISMO CLIENT CONCIERGE (CONVERSATIONS API)
# ==========================================
def tool_16_wismo_client_concierge(dry_run: bool = False):
    hs_key = st.secrets.get("hubspot", {}).get("service_key")
    if not hs_key: return "🚨 CRITICAL CRASH: HubSpot API Key not found in st.secrets."
    
    ms_token = st.secrets.get("machship", {}).get("MACHSHIP_API_TOKEN")
    if not ms_token: return "🚨 CRITICAL CRASH: Machship API Token not found in st.secrets."
    
    hs_headers = {
        "Authorization": f"Bearer {hs_key}",
        "Content-Type": "application/json"
    }
    
    hs_threads_url = get_secure_endpoint("hs_threads", "aHR0cHM6Ly9hcGkuaHViYXBpLmNvbS9jb252ZXJzYXRpb25zL3YzL2NvbnZlcnNhdGlvbnMvdGhyZWFkcw==")
    
    try:
        cutoff_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=12)
        cutoff_ms = int(cutoff_dt.timestamp() * 1000)
        
        all_threads = []
        base_url = f"{hs_threads_url}?limit=100&sort=latestMessageTimestamp&latestMessageTimestampAfter={cutoff_ms}"
        target_url = base_url
        
        for _ in range(2): 
            threads_resp = requests.get(target_url, headers=hs_headers, timeout=15)
            if threads_resp.status_code != 200:
                if not all_threads:
                    return f"🚨 CRITICAL CRASH: HubSpot API Request Failed (HTTP {threads_resp.status_code}). Raw Payload: {threads_resp.text}"
                break
                
            data = threads_resp.json()
            all_threads.extend(data.get("results", []))
            
            paging_after = data.get("paging", {}).get("next", {}).get("after")
            if paging_after:
                target_url = f"{base_url}&after={paging_after}"
            else:
                break

        if not all_threads:
            return "WISMO Sweep Complete. No conversational threads found in the API response."
            
        master_agent_id = None
        target_email = "jim@freightcompaniesaustralia.com.au"
        
        try:
            owners_url = get_secure_endpoint("hs_owners", "aHR0cHM6Ly9hcGkuaHViYXBpLmNvbS9jcm0vdjMvb3duZXJz")
            owners_resp = requests.get(owners_url, headers=hs_headers, timeout=10)
            if owners_resp.status_code == 200:
                owners_data = owners_resp.json().get("results", [])
                for owner in owners_data:
                    if owner.get("email") == target_email:
                        master_agent_id = f"A-{owner.get('id')}"
                        break
        except Exception:
            pass

        if not master_agent_id:
            for t in all_threads:
                assigned = str(t.get("assignedTo") or t.get("assigneeId") or "")
                if assigned and assigned.lower() != "none":
                    if assigned.isdigit():
                        master_agent_id = f"A-{assigned}"
                        break
                    elif assigned.startswith(("A-", "B-", "V-")):
                        master_agent_id = assigned
                        break

        open_threads = [t for t in all_threads if str(t.get("status", "")).upper() == "OPEN"]
        
        try:
            open_threads = sorted(open_threads, key=lambda x: str(x.get("latestMessageTimestamp", "")), reverse=True)
        except Exception:
            pass
            
        if not open_threads:
            return "WISMO Sweep Complete. Filtered out all closed/historical threads. No live open threads require action."
            
        action_log = []
        actioned_count = 0
        
        for thread in open_threads:
            try:
                if actioned_count >= 20:
                    break
                    
                thread_id = thread.get("id")
                
                messages_resp = requests.get(f"{hs_threads_url}/{thread_id}/messages", headers=hs_headers, timeout=15)
                if messages_resp.status_code != 200:
                    continue
                
                messages = messages_resp.json().get("results", [])
                if not messages: continue

                external_messages = [m for m in messages if m.get("type") == "MESSAGE"]
                
                if len(external_messages) > 1:
                    continue
                    
                if len(external_messages) == 1:
                    is_internal_sender = False
                    for s in external_messages[0].get("senders", []):
                        deliv_id = s.get("deliveryIdentifier")
                        email_val = ""
                        if isinstance(deliv_id, dict):
                            email_val = str(deliv_id.get("value", "")).lower()
                        elif isinstance(deliv_id, str):
                            email_val = str(deliv_id).lower()
                            
                        if "@freightcompaniesaustralia.com.au" in email_val:
                            is_internal_sender = True
                            break
                            
                    if is_internal_sender:
                        continue
                
                channel_id = None
                channel_account_id = None
                sender_actor_id = None
                customer_actor_id = None
                customer_delivery_identifier = None
                
                assignee_id = thread.get("assigneeId") or thread.get("assignedTo")
                if assignee_id and str(assignee_id).lower() != "none":
                    val = str(assignee_id)
                    if val.isdigit():
                        sender_actor_id = f"A-{val}"
                    else:
                        sender_actor_id = val
                
                for m in messages:
                    if not channel_id and m.get("channelId"):
                        channel_id = str(m.get("channelId"))
                    if not channel_account_id and m.get("channelAccountId"):
                        channel_account_id = str(m.get("channelAccountId"))
                    
                    senders = m.get("senders", [])
                    if not isinstance(senders, list):
                        senders = []
                        
                    for s in senders:
                        actor = str(s.get("actorId", ""))
                        if actor and actor.lower() != "none":
                            if actor.isdigit():
                                if not sender_actor_id: sender_actor_id = f"A-{actor}"
                            elif actor.startswith(("A-", "B-")):
                                if not sender_actor_id: sender_actor_id = actor
                            elif not actor.startswith("S-"): 
                                if not customer_actor_id: customer_actor_id = actor
                                
                                deliv_id = s.get("deliveryIdentifier")
                                if deliv_id and not customer_delivery_identifier:
                                    if isinstance(deliv_id, str):
                                        customer_delivery_identifier = {"type": "HS_EMAIL_ADDRESS", "value": deliv_id}
                                    elif isinstance(deliv_id, dict) and "value" in deliv_id:
                                        customer_delivery_identifier = {"type": deliv_id.get("type", "HS_EMAIL_ADDRESS"), "value": deliv_id.get("value")}
                                    
                    root_actor = str(m.get("senderActorId", ""))
                    if root_actor and root_actor.lower() != "none" and not sender_actor_id:
                        if root_actor.isdigit():
                            sender_actor_id = f"A-{root_actor}"
                        elif root_actor.startswith(("A-", "B-")):
                            sender_actor_id = root_actor
                            
                if not sender_actor_id or str(sender_actor_id).lower() == "none":
                    sender_actor_id = master_agent_id
                        
                if not sender_actor_id or not str(sender_actor_id).startswith(("A-", "B-")):
                    action_log.append(f"Thread {thread_id}: CRITICAL ERROR - Cannot deduce a valid Agent ID. Thread skipped.")
                    actioned_count += 1
                    continue

                def build_payload(msg_type, text):
                    p = { "type": msg_type, "text": text, "senderActorId": sender_actor_id }
                    
                    if channel_id and channel_account_id:
                        p["channelId"] = str(channel_id)
                        p["channelAccountId"] = str(channel_account_id)
                        
                    if msg_type == "MESSAGE" and customer_delivery_identifier:
                        recipient_node = {
                            "recipientField": "TO",
                            "deliveryIdentifiers": [customer_delivery_identifier]
                        }
                        if customer_actor_id:
                            recipient_node["actorId"] = customer_actor_id
                            
                        p["recipients"] = [recipient_node]
                        
                    if msg_type == "MESSAGE" and not (channel_id and channel_account_id and customer_delivery_identifier):
                        p["type"] = "COMMENT"
                        p["text"] = f"BOOF WISMO Alert [DRAFT: Missing routing data (channelId: {bool(channel_id)}, channelAccountId: {bool(channel_account_id)}, customer_email: {bool(customer_delivery_identifier)}). Cannot send natively]:\n\n{text}"
                        p.pop("recipients", None)
                        p.pop("channelId", None)
                        p.pop("channelAccountId", None)
                        
                    return p
                
                latest_customer_time = ""
                latest_agent_time = ""
                for m in messages:
                    m_time = str(m.get("createdAt", ""))
                    
                    is_agent = False
                    if m.get("type") == "COMMENT":
                        is_agent = True
                    else:
                        senders = m.get("senders", [])
                        if not isinstance(senders, list): senders = []
                        for s in senders:
                            actor = str(s.get("actorId", ""))
                            if actor.startswith("A-") or actor.startswith("B-"):
                                is_agent = True
                        
                        root_actor = str(m.get("senderActorId", ""))
                        if root_actor.startswith("A-") or root_actor.startswith("B-"):
                            is_agent = True
                    
                    if is_agent:
                        if m_time > latest_agent_time:
                            latest_agent_time = m_time
                    else:
                        if m_time > latest_customer_time:
                            latest_customer_time = m_time
                            
                if latest_agent_time and latest_customer_time and latest_agent_time >= latest_customer_time:
                    continue
                
                msg_texts = []
                for m in messages:
                    if m.get("type") == "COMMENT": continue
                    
                    m_text = str(m.get("text") or "")
                    m_rich = str(m.get("richText") or "")
                    m_subject = str(m.get("subject") or "")
                    
                    combined_node = f"{m_subject} {m_text} {m_rich}".strip()
                    if combined_node:
                        msg_texts.append(combined_node)
                        
                if not msg_texts: continue
                combined_text = "\n".join(msg_texts)
                
                extract_prompt = f"Extract all freight tracking/consignment numbers (e.g., MS123456, FGY000000990, 87654321, etc.) from this text. CRITICAL INSTRUCTION 1: Ignore phone numbers and ABNs. CRITICAL INSTRUCTION 2: If no freight reference is explicitly found, you MUST return an empty array []. DO NOT hallucinate, invent, or guess tracking numbers based on the examples. Output ONLY a raw JSON array of strings. Example: [\"MS123456\"] or []. Text: {combined_text}"
                
                try:
                    extracted_refs_str = call_gemini_api(extract_prompt, json_mode=True)
                    raw_refs = json.loads(extracted_refs_str)
                    if isinstance(raw_refs, dict):
                        for val in raw_refs.values():
                            if isinstance(val, list):
                                raw_refs = val
                                break
                    if not isinstance(raw_refs, list):
                        raw_refs = []
                        
                    refs = []
                    for r in raw_refs:
                        clean_r = str(r).strip().upper()
                        if clean_r and clean_r in combined_text.upper():
                            refs.append(clean_r)
                            
                except Exception as e:
                    action_log.append(f"Thread {thread_id}: LLM Extraction Crash: {str(e)}")
                    actioned_count += 1
                    continue
                    
                if not refs or len(refs) == 0:
                    if not dry_run:
                        note_payload = build_payload("COMMENT", "BOOF WISMO Alert: No valid tracking reference detected in this thread. Skipping.")
                        req = requests.post(f"{hs_threads_url}/{thread_id}/messages", headers=hs_headers, json=note_payload, timeout=15)
                        if req.status_code not in [200, 201]:
                            err = f"Thread {thread_id} POST Note Failed (HTTP {req.status_code}). Payload: {req.text}"
                            action_log.append(err)
                            actioned_count += 1
                            continue
                            
                    action_log.append(f"Thread {thread_id}: No connote found. Left skip note.")
                    actioned_count += 1
                    continue
                
                ms_headers_dict = { 
                    "token": ms_token, 
                    "Content-Type": "application/json",
                    "Accept": "application/json" 
                }
                
                tracking_info = None
                ms_consign_id = None
                has_pod = False
                carrier_source = "None"
                ms_diagnostics = []
                final_connote = ""
                
                for extracted_ref in refs:
                    connote = str(extracted_ref).upper().strip()
                    ms_diagnostics.append(f"Evaluating: {connote}")
                    
                    if connote.startswith("MS"):
                        ms_id = re.sub(r"\D", "", connote)
                        get_url = get_secure_endpoint("machship_get", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvZ2V0Q29uc2lnbm1lbnQ/aWQ9")
                        try:
                            r = requests.get(f"{get_url}{ms_id}", headers=ms_headers_dict, timeout=15)
                            if r.status_code == 200:
                                obj = r.json().get("object")
                                if obj:
                                    tracking_info = json.dumps(obj)
                                    ms_consign_id = obj.get("id")
                                    has_pod = obj.get("attachmentCount", 0) > 0
                                    carrier_source = "Machship"
                            else:
                                ms_diagnostics.append(f"GET HTTP {r.status_code}")
                        except Exception as e:
                            ms_diagnostics.append(f"GET Crash: {sanitize_error_log(str(e))}")

                    if not tracking_info:
                        ms_post_urls = [
                            ("Carrier_ID", get_secure_endpoint("machship_carrier_id", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlDYXJyaWVyQ29uc2lnbm1lbnRJZD9pbmNsdWRlQ2hpbGRDb21wYW5pZXM9dHJ1ZQ==")),
                            ("Ref_1", get_secure_endpoint("machship_ref1", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UxP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl")),
                            ("Ref_2", get_secure_endpoint("machship_ref2", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UyP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl"))
                        ]
                        for search_type, url in ms_post_urls:
                            try:
                                r = requests.post(url, headers=ms_headers_dict, json=[connote], timeout=15)
                                if r.status_code == 200:
                                    data = r.json()
                                    obj_list = data.get("object")
                                    
                                    if obj_list:
                                        if isinstance(obj_list, list) and len(obj_list) > 0:
                                            obj = obj_list[0]
                                        elif isinstance(obj_list, dict):
                                            obj = obj_list
                                        else:
                                            continue
                                            
                                        tracking_info = json.dumps(obj)
                                        ms_consign_id = obj.get("id")
                                        has_pod = obj.get("attachmentCount", 0) > 0
                                        carrier_source = "Machship"
                                        break
                                else:
                                    ms_diagnostics.append(f"{search_type}: HTTP {r.status_code}")
                            except Exception as e:
                                ms_diagnostics.append(f"{search_type} Crash")

                    if not tracking_info:
                        tv_token = st.secrets.get("transvirtual", {}).get("TRANSVIRTUAL_API_KEY")
                        if tv_token:
                            tv_headers = {
                                "Authorization": tv_token,
                                "Content-Type": "application/json",
                                "Accept": "application/json"
                            }
                            tv_status_url = get_secure_endpoint("tv_status", "aHR0cHM6Ly9hcGkudHJhbnN2aXJ0dWFsLmNvbS5hdS9hcGkvQ29uc2lnbm1lbnRTdGF0dXM=")
                            try:
                                tv_payload = {"Number": connote}
                                tv_resp = requests.post(tv_status_url, headers=tv_headers, json=tv_payload, timeout=10)
                                if tv_resp.status_code == 200 and "Missing" not in tv_resp.text:
                                    tv_data = tv_resp.json().get("Data", tv_resp.json())
                                    if tv_data:
                                        tracking_info = json.dumps(tv_data)
                                        has_pod = False
                                        carrier_source = "Transvirtual"
                            except Exception as e:
                                ms_diagnostics.append(f"TV Crash: {sanitize_error_log(str(e))}")
                                
                    if tracking_info:
                        final_connote = connote
                        break
                        
                if not tracking_info:
                    diag_str = " | ".join(ms_diagnostics) if ms_diagnostics else "Unknown Failure"
                    all_refs_str = ", ".join(refs)
                    fallback_msg = f"BOOF WISMO Alert: Could not locate Machship or Transvirtual data for references: {all_refs_str}. Reassigning to human broker.\nDiagnostics: {diag_str}"
                    
                    if not dry_run:
                        note_payload = build_payload("COMMENT", fallback_msg)
                        req = requests.post(f"{hs_threads_url}/{thread_id}/messages", headers=hs_headers, json=note_payload, timeout=15)
                        if req.status_code not in [200, 201]:
                            err = f"Thread {thread_id} POST Note Failed (HTTP {req.status_code}). Payload: {req.text}"
                            action_log.append(err)
                            actioned_count += 1
                            continue
                            
                    action_log.append(f"Thread {thread_id}: References not found across API pipelines. Left internal note. {diag_str}")
                    actioned_count += 1
                    continue
                    
                connote = final_connote
                current_date = datetime.datetime.now().strftime("%Y-%m-%d")
                
                eval_prompt = f"""
                You are the BOOF Freight Concierge Data Extraction Module.
                Analyze this JSON freight tracking data retrieved via {carrier_source}: {tracking_info}
                Today's Date is: {current_date}
                
                Task:
                1. PRIMARY DIRECTIVE: If the freight is 'Delivered', 'Complete', or 'Completed', the sentiment is ALWAYS 'POSITIVE', regardless of any historical delays or ETA breaches.
                2. SECONDARY DIRECTIVE: If the freight is NOT delivered, evaluate the ETA. If Today's Date ({current_date}) is strictly greater than the Expected Delivery Date or ETA, classify sentiment as 'NEGATIVE'.
                3. TERTIARY DIRECTIVE: For freight that is NOT delivered and NOT past its ETA: POSITIVE = (Booked, On board for delivery, Manifested, In Transit); NEGATIVE = (Delayed, Exception, Damaged, Lost, Missed Pickup).
                4. Extract the following properties EXACTLY from the JSON. If a property is not found, output "Unknown".
                - sender_company_name
                - sender_suburb
                - receiver_company_name
                - receiver_suburb
                - status (e.g. In Transit, Delivered, Scanned into Depot)
                - delivery_time (If delivered, format strictly as h.mma or h.mmpm, e.g., 11.26am. If not delivered, leave blank)
                - delivery_date (If delivered, format strictly as DD-MM-YYYY. If not delivered, leave blank)
                - eta_date (If not delivered, format strictly as DD-MM-YYYY. If delivered, leave blank)
                
                Return ONLY a valid JSON object matching the exact keys requested above, plus the 'sentiment' key. Do not write the final email message.
                """
                
                try:
                    eval_str = call_gemini_api(eval_prompt, json_mode=True)
                    eval_res = json.loads(eval_str)
                except Exception as e:
                    action_log.append(f"Thread {thread_id} AI Eval Parse Error: {str(e)}")
                    actioned_count += 1
                    continue
                    
                sentiment = eval_res.get("sentiment", "NEGATIVE")
                sender_comp = eval_res.get("sender_company_name", "Unknown Sender")
                sender_sub = eval_res.get("sender_suburb", "Unknown Suburb")
                receiver_comp = eval_res.get("receiver_company_name", "Unknown Receiver")
                receiver_sub = eval_res.get("receiver_suburb", "Unknown Suburb")
                status_str = eval_res.get("status", "Unknown Status")
                deliv_time = eval_res.get("delivery_time", "")
                deliv_date = eval_res.get("delivery_date", "")
                eta_date = eval_res.get("eta_date", "Unknown ETA")
                
                if sentiment == "POSITIVE":
                    status_str_lower = status_str.lower()
                    is_delivered = any(keyword in status_str_lower for keyword in ["delivered", "complete", "completed"])
                    
                    if is_delivered:
                        time_segment = f" at {deliv_time}" if deliv_time else ""
                        date_segment = f" on {deliv_date}" if deliv_date else " recently"
                        status_line = f"Consignment {connote} was completed/delivered{time_segment}{date_segment}."
                    else:
                        status_line = f"Consignment {connote} is currently {status_str}. Expected delivery is by {eta_date}."
                        
                    pod_line = ""
                    if carrier_source == "Machship":
                        pod_line = f"\n\nMore tracking information is available on Machship. Please log in, search for {connote}."
                    elif carrier_source == "Transvirtual":
                        pod_line = f"\n\nLive tracking and documentation are accessible via the carrier's direct tracking portal using your consignment number: {connote}."
                        
                    base_message = f"Thank you for your enquiry about connote {connote}\n\nPicked up from {sender_comp}, {sender_sub}\nFor delivery to {receiver_comp}, {receiver_sub}\n\n{status_line}{pod_line}\n\nAs this is a good news email, it has been responded to automatically by FCA's AI assistant (BOOF). If the email response isn't accurate or appropriate, that's Marshall's fault. Please forward this email directly to marshall@fca.net.au and he will investigate."
                    
                    if not dry_run:
                        reply_payload = build_payload("MESSAGE", base_message)
                        req = requests.post(f"{hs_threads_url}/{thread_id}/messages", headers=hs_headers, json=reply_payload, timeout=15)
                        if req.status_code not in [200, 201]:
                            err = f"Thread {thread_id} POST Reply Failed (HTTP {req.status_code}). Payload: {req.text}"
                            action_log.append(err)
                            actioned_count += 1
                            continue
                        
                        close_req = requests.patch(f"{hs_threads_url}/{thread_id}", headers=hs_headers, json={"status": "CLOSED"}, timeout=15)
                        if close_req.status_code in [200, 201, 204]:
                            action_log.append(f"Thread {thread_id}: POSITIVE status for {connote} via {carrier_source}. Replied to customer and closed thread.")
                        else:
                            action_log.append(f"Thread {thread_id}: Reply sent, but automated thread closure failed (HTTP {close_req.status_code}). Reason: {close_req.text}")
                    else:
                        action_log.append(f"[DRY RUN] Thread {thread_id}: POSITIVE status for {connote} via {carrier_source}. Would reply to customer and close thread.")
                    
                else:
                    base_message = f"ACTION REQUIRED: {connote} is delayed/ETA breached. Current status is {status_str}."
                    if not dry_run:
                        note_payload = build_payload("COMMENT", f"BOOF WISMO Alert: {base_message}")
                        req = requests.post(f"{hs_threads_url}/{thread_id}/messages", headers=hs_headers, json=note_payload, timeout=15)
                        if req.status_code not in [200, 201]:
                            err = f"Thread {thread_id} POST Note Failed (HTTP {req.status_code}). Payload: {req.text}"
                            action_log.append(err)
                            actioned_count += 1
                            continue
                            
                    action_log.append(f"Thread {thread_id}: NEGATIVE status for {connote} via {carrier_source}. Left internal broker note.")
                    
                actioned_count += 1
            except Exception as loop_e:
                action_log.append(f"Thread {thread_id} Crash: {str(loop_e)}")
                actioned_count += 1
                continue
                
        summary_string = "WISMO Sweep Complete.\n" + "\n".join(action_log) if action_log else "WISMO Sweep Complete. No new actionable threads."
        return f"SYSTEM INSTRUCTION TO AI: Output the following log EXACTLY as written. Do not summarize it. \n\n{summary_string}"
            
    except Exception as e:
        return f"🚨 CRITICAL CRASH: {sanitize_error_log(str(e))}"

# ==========================================
# TOOL 13: PROACTIVE CUSTOMER NOTIFICATION
# ==========================================
def tool_13_proactive_customer_notification(dry_run: bool = False) -> str:
    action_log = []
    action_log.append("Initiating Machship temporal anomaly sweep (168 hours)...")
    
    try:
        ms_token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
        hs_key = st.secrets.get("hubspot", {}).get("service_key")
        
        base_url = get_secure_endpoint("machship_recent", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvZ2V0UmVjZW50bHlDcmVhdGVkT3JVcGRhdGVkQ29uc2lnbm1lbnRz")
        ms_headers = { "token": ms_token, "Content-Type": "application/json" }
        
        raw_consignments = []
        action_log.append("Executing 24-hour temporal chunking (7 slices) to bypass Machship record limits...")
        
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        
        for i in range(7):
            chunk_to = now_utc - datetime.timedelta(days=i)
            chunk_from = now_utc - datetime.timedelta(days=i+1)
            
            params = {
                "fromDateUtc": chunk_from.strftime('%Y-%m-%dT%H:%M:%S'),
                "toDateUtc": chunk_to.strftime('%Y-%m-%dT%H:%M:%S')
            }
            
            try:
                resp = requests.get(base_url, headers=ms_headers, params=params, timeout=15)
                if resp.status_code == 200:
                    page_data = resp.json().get('object', [])
                    if page_data:
                        raw_consignments.extend(page_data)
                else:
                    action_log.append(f"CRITICAL ERROR: Machship API rejected chunk {i+1} (HTTP {resp.status_code}).")
            except Exception as e:
                action_log.append(f"Chunk {i+1} timeout/crash: {sanitize_error_log(str(e))}")
                
        unique_data = {item.get('id'): item for item in raw_consignments if item.get('id')}
        active_data = list(unique_data.values())
        
        action_log.append(f"Data ingestion complete. {len(active_data)} unique consignments retrieved across 168 hours.")
        
        error_statuses = ['exception', 'delayed', 'held', 'damaged', 'missed pickup', 'partial']
        success_statuses = ['delivered', 'on board for delivery', 'partially delivered', 'awaiting collection', 'completed', 'complete']
        
        anomalies_detected = []
        resolutions_detected = []
        current_date = datetime.datetime.now().date()
        
        def safe_extract_date(date_str):
            if not date_str: return None
            try:
                return datetime.datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
            except:
                return None

        for item in active_data:
            c_id = item.get('consignmentNumber')
            carrier = item.get('carrier', {}).get('name', 'Unknown Carrier')
            
            track_node = item.get('consignmentTrackingStatus') or {}
            track_status = str(track_node.get('name') or '').lower()
            
            gen_node = item.get('status') or {}
            gen_status = str(gen_node.get('name') or '').lower()
            
            status_set = {track_status, gen_status}
            
            ignore_keywords = ['quote', 'quoted', 'unmanifested']
            if any(kw in s for kw in ignore_keywords for s in status_set):
                continue
            
            raw_eta = item.get('etaLocal') or item.get('eta') or item.get('expectedDeliveryDate')
            eta_date = safe_extract_date(raw_eta)
            
            acc_node = item.get('companyCarrierAccount') or {}
            acc_name = str(acc_node.get('name') or acc_node.get('accountCode') or '').upper()
            
            to_node = item.get('despatch', {}).get('toLocation', {}) or item.get('toLocation', {})
            destination = f"{to_node.get('suburb', 'Unknown')}, {to_node.get('state', 'Unknown')}"
            
            is_error = any(s in error_statuses for s in status_set)
            is_success = any(s in success_statuses for s in status_set)
            is_breached = False
            
            if eta_date and eta_date < current_date and not is_success:
                is_breached = True
                
            client_category = "Standard"
            if "CALM" in acc_name: client_category = "CALM"
            elif "ACRRM" in acc_name: client_category = "ACRRM"
            elif "BOA" in acc_name: client_category = "BOA"
            elif "AC SOLAR" in acc_name or "REGROUP" in acc_name: client_category = acc_name
                
            if is_error or is_breached:
                anomalies_detected.append({
                    "connote": c_id,
                    "carrier": carrier,
                    "destination": destination,
                    "status": (track_status or gen_status).title(),
                    "reason": "Carrier Error Status" if is_error else "ETA Breach",
                    "client_category": client_category
                })
            elif is_success:
                resolutions_detected.append({
                    "connote": c_id,
                    "carrier": carrier,
                    "destination": destination,
                    "status": (track_status or gen_status).title(),
                    "client_category": client_category
                })
                
        action_log.append(f"Analysis complete. {len(anomalies_detected)} anomalies and {len(resolutions_detected)} successes identified.")
        
        ticket_url = get_secure_endpoint("hubspot_tickets", "aHR0cHM6Ly9hcGkuaHViYXBpLmNvbS9jcm0vdjMvb2JqZWN0cy90aWNrZXRz")
        search_url = get_secure_endpoint("hubspot_search", "aHR0cHM6Ly9hcGkuaHViYXBpLmNvbS9jcm0vdjMvb2JqZWN0cy90aWNrZXRzL3NlYXJjaA==")
        hs_headers = { "Authorization": f"Bearer {hs_key}", "Content-Type": "application/json" }

        def get_existing_ticket_id(connote_num):
            payload = {
                "filterGroups": [{"filters": [{"propertyName": "subject", "operator": "CONTAINS_TOKEN", "value": connote_num}]}]
            }
            try:
                resp = requests.post(search_url, headers=hs_headers, json=payload, timeout=10)
                if resp.status_code == 200 and resp.json().get('total', 0) > 0:
                    return resp.json()['results'][0]['id']
            except:
                pass
            return None

        for res in resolutions_detected:
            ticket_id = get_existing_ticket_id(res['connote'])
            if ticket_id:
                action_log.append(f"-> {res['connote']}: Prior anomaly resolved. Generating success draft.")
                
                success_prompt = f"""
                You are a professional freight customer service manager. 
                Translate the following successful carrier status into a polite update for the client, advising them that their previously delayed freight has now progressed.
                Carrier: {res['carrier']}
                Destination: {res['destination']}
                Raw Status: {res['status']}
                CRITICAL INSTRUCTION 1: You must strictly avoid using the words "proactive" or "proactively".
                CRITICAL INSTRUCTION 2: Return ONLY a valid JSON object with a single key 'client_message' containing the email body. Do not include sign-offs or greetings.
                """
                
                try:
                    translation_response = call_gemini_api(success_prompt, json_mode=True)
                    client_message = json.loads(translation_response).get("client_message", "Your freight has successfully progressed.")
                except:
                    client_message = f"Good news: Your consignment ({res['status']}) has successfully progressed."

                if not dry_run:
                    note_url = f"[https://api.hubapi.com/crm/v3/objects/notes](https://api.hubapi.com/crm/v3/objects/notes)"
                    note_props = {
                        "hs_note_body": f"=== RESOLUTION DETECTED ===\nDraft Update for Client:\n\n{client_message}",
                        "hs_timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    }
                    try:
                        note_resp = requests.post(note_url, headers=hs_headers, json={"properties": note_props}, timeout=10)
                        if note_resp.status_code in [200, 201]:
                            note_id = note_resp.json()['id']
                            assoc_url = f"[https://api.hubapi.com/crm/v3/associations/notes/tickets/batch/create](https://api.hubapi.com/crm/v3/associations/notes/tickets/batch/create)"
                            requests.post(assoc_url, headers=hs_headers, json={"inputs": [{"from": {"id": note_id}, "to": {"id": ticket_id}, "type": "note_to_ticket"}]}, timeout=10)
                    except Exception as e:
                        action_log.append(f"-> {res['connote']}: Resolution Note Sync Failed: {sanitize_error_log(str(e))}")

        for anomaly in anomalies_detected:
            if anomaly["client_category"] == "ACRRM":
                action_log.append(f"-> {anomaly['connote']}: Bypassed automated client translation (Tier 1 Medical).")
                continue
            
            ticket_id = get_existing_ticket_id(anomaly['connote'])
            if ticket_id:
                action_log.append(f"-> {anomaly['connote']}: Skipped. HubSpot ticket already exists.")
                continue

            translation_prompt = f"""
            You are a professional freight customer service manager. 
            Translate the following carrier error into a polite and professional update for the client.
            Carrier: {anomaly['carrier']}
            Destination: {anomaly['destination']}
            Raw Error: {anomaly['status']}
            Trigger Reason: {anomaly['reason']}
            
            CRITICAL INSTRUCTION 1: You must strictly avoid using the words "proactive" or "proactively".
            CRITICAL INSTRUCTION 2: Return ONLY a valid JSON object with a single key 'client_message' containing the email body. Do not include sign-offs or greetings.
            """
            
            try:
                translation_response = call_gemini_api(translation_prompt, json_mode=True)
                base_message = json.loads(translation_response).get("client_message", "We are currently investigating a tracking anomaly with your freight.")
                disclaimer = "\n\nPlease be aware that carrier track and trace sometimes produces false negatives. All may be well with this consignment, but we like to be sure."
                client_message = f"{base_message}{disclaimer}"
            except Exception as e:
                action_log.append(f"-> {anomaly['connote']}: Gemini Translation Crash: {sanitize_error_log(str(e))}")
                client_message = f"Automated Alert: An anomaly ({anomaly['status']}) has been detected. We are investigating.\n\nPlease be aware that carrier track and trace sometimes produces false negatives. All may be well with this consignment, but we like to be sure."
            
            if dry_run:
                action_log.append(f"[DRY RUN] {anomaly['connote']} | Target: {anomaly['client_category']}\nProposed Draft:\n{client_message}")
            else:
                ticket_props = {
                    "subject": f"Proactive Alert: {anomaly['connote']} ({anomaly['carrier']})",
                    "content": f"ANOMALY DETECTED ({anomaly['client_category']}):\n\nConnote: {anomaly['connote']}\nDestination: {anomaly['destination']}\nRaw Status: {anomaly['status']}\n\n=== SUGGESTED CLIENT MESSAGE ===\n{client_message}",
                    "hs_pipeline": "0",
                    "hs_pipeline_stage": "1"
                }
                
                clean_props = sanitize_hubspot_payload(ticket_props)
                try:
                    resp = requests.post(ticket_url, headers=hs_headers, json={"properties": clean_props}, timeout=15)
                    if resp.status_code in [200, 201]:
                        action_log.append(f"-> {anomaly['connote']}: HubSpot anomaly ticket generated successfully.")
                    else:
                        action_log.append(f"-> {anomaly['connote']}: HubSpot POST failed (HTTP {resp.status_code}).")
                except Exception as e:
                    action_log.append(f"-> {anomaly['connote']}: HubSpot Injection Crash: {sanitize_error_log(str(e))}")

    except Exception as e:
        action_log.append(f"🚨 TOOL 13 CRASH: {sanitize_error_log(str(e))}")
        
    summary_string = "\n".join(action_log)
    return f"SYSTEM INSTRUCTION TO AI: You MUST output the following log EXACTLY as written inside a markdown code block. Do not summarize, paraphrase, or alter it. \n\n{summary_string}"

# ==========================================
# BACKWARD COMPATIBILITY ALIASES 
# ==========================================
def tool_11_transit_delay_engine(*args, **kwargs):
    return tool_10_freight_alert_automator(dry_run=kwargs.get('dry_run', False))
    
def tool_10_temporal_anomaly_detector(*args, **kwargs):
    return tool_10_freight_alert_automator()
