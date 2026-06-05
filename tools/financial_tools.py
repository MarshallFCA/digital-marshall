# ==========================================
# TOOL 17: KERMIT (CartonCloud Machship Invoice Reconciliation Tool)
# ==========================================
def tool_17_kermit_reconciliation_engine(start_date: str, end_date: str, customer_name: str = "Rhino") -> str:
    import datetime
    import pandas as pd
    import requests
    import streamlit as st
    import json
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from tools.core_utils import get_secure_endpoint, sanitize_error_log, get_cartoncloud_token
    
    def parse_flexible_date(date_string: str) -> datetime.date:
        import re
        clean_str = re.sub(r'(?i)(st|nd|rd|th)', '', str(date_string)).strip()
        formats = [
            "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", 
            "%d %b %Y", "%d %B %Y", "%Y/%m/%d", "%B %d %Y", "%b %d %Y"
        ]
        for fmt in formats:
            try:
                return datetime.datetime.strptime(clean_str, fmt).date()
            except ValueError:
                continue
        return datetime.datetime.now().date()

    try:
        start_dt = parse_flexible_date(start_date)
        end_dt = parse_flexible_date(end_date)
    except Exception as e:
        return f"CRITICAL CRASH: Date Engine Failure. {sanitize_error_log(str(e))}"
        
    diagnostic_logs = []
    
    cc_tenant_id = st.secrets["cartoncloud"]["tenant_id"].strip()
    cc_base_url = get_secure_endpoint("cartoncloud_base", "aHR0cHM6Ly9hcGkuY2FydG9uY2xvdWQuY29t")
    cc_token = get_cartoncloud_token()
    
    if "Error" in cc_token:
        return f"CRITICAL CRASH: CartonCloud Authentication Failure. {cc_token}"
        
    cc_headers = {
        "Accept-Version": "1",
        "Authorization": f"Bearer {cc_token}",
        "Content-Type": "application/json"
    }
    
    raw_orders = []
    
    # 2-Stage Fortress Sweep (20 pages = 2,000 orders to ensure we hit April history)
    for page in range(1, 21): 
        try:
            # Pagination correctly formatted in URL per API Docs
            search_url = f"{cc_base_url}/tenants/{cc_tenant_id}/outbound-orders/search?page={page}&size=100"
            
            # Primary Target: Use the exact Rhino UUID we extracted earlier
            target_uuid = "4c11a442-be53-4525-9b01-a4f237f2fb2e" if customer_name.lower() == "rhino" else ""
            
            payload_1 = {
                "condition": {
                    "type": "EqualsCondition",
                    "field": { "type": "JsonField", "pointer": "/customer/id" },
                    "value": { "type": "ValueField", "value": target_uuid }
                },
                "sort": [{"field": {"type": "JsonField", "pointer": "/id"}, "direction": "DESC"}]
            }
            
            # Ironclad Fallback: Match all orders where type = "OUTBOUND"
            payload_2 = {
                "condition": {
                    "type": "EqualsCondition",
                    "field": { "type": "JsonField", "pointer": "/type" },
                    "value": { "type": "ValueField", "value": "OUTBOUND" }
                },
                "sort": [{"field": {"type": "JsonField", "pointer": "/id"}, "direction": "DESC"}]
            }

            # Execute Primary
            resp = requests.post(search_url, headers=cc_headers, json=payload_1 if target_uuid else payload_2, timeout=15)
            
            if resp.status_code == 200:
                page_data = resp.json()
            else:
                diagnostic_logs.append(f"UUID Filter Rejected (HTTP {resp.status_code}). Executing Type=OUTBOUND Fallback.")
                # Execute Fallback
                resp = requests.post(search_url, headers=cc_headers, json=payload_2, timeout=15)
                
                if resp.status_code == 200:
                    page_data = resp.json()
                else:
                    # Log the exact error string so we can see what CartonCloud hates
                    diagnostic_logs.append(f"Ultimate Fallback Rejected. HTTP {resp.status_code} - {resp.text}")
                    break
                    
            if not page_data: break
            raw_orders.extend(page_data)

        except Exception as e:
            diagnostic_logs.append(f"CartonCloud Sweep Crash: {sanitize_error_log(str(e))}")
            break

    matrix_data = []
    
    for order in raw_orders:
        o_customer = order.get("customer", {}).get("name", "")
        if customer_name.lower() not in o_customer.lower():
            continue
            
        timestamps = order.get("timestamps", {})
        o_date_str = timestamps.get("dispatched", {}).get("time") or timestamps.get("created", {}).get("time")
        
        if not o_date_str: continue
        
        try:
            o_date = datetime.datetime.strptime(o_date_str[:10], "%Y-%m-%d").date()
        except Exception:
            continue
            
        if not (start_dt <= o_date <= end_dt):
            continue
            
        cust_ref = order.get("references", {}).get("customer", "")
        
        # Financials will pull 0.0 until CC Support updates your token permissions.
        financials = order.get("financials", {})
        cc_cost = financials.get("totalCost") or financials.get("invoiceAmount") or order.get("totalCost") or order.get("calculatedCharges", 0.0)
        
        matrix_data.append({
            "CartonCloud ID": order.get("id"),
            "Date": o_date_str[:10],
            "Customer Reference": cust_ref,
            "CartonCloud Status": order.get("status", {}).get("name", "UNKNOWN") if isinstance(order.get("status"), dict) else order.get("status", "UNKNOWN"),
            "Warehouse Cost": float(cc_cost) if cc_cost else 0.0,
            "Machship Cost": 0.0,
            "Machship Sell": 0.0,
            "Machship Status": "Not Found",
            "Machship Carrier": "N/A"
        })

    if not matrix_data:
        log_output = " | ".join(diagnostic_logs) if diagnostic_logs else "Clean"
        return f"KERMIT Sweep Complete. No valid orders found for {customer_name} between {start_dt.strftime('%Y-%m-%d')} and {end_dt.strftime('%Y-%m-%d')}. Diagnostics: {log_output}"

    ms_token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
    ms_headers = { "token": ms_token, "Content-Type": "application/json" }
    
    ms_urls = [
        get_secure_endpoint("machship_ref1", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UxP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl"),
        get_secure_endpoint("machship_ref2", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UyP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl")
    ]
    
    for row in matrix_data:
        ref = row["Customer Reference"]
        if not ref: continue
        
        found = False
        for url in ms_urls:
            if found: break
            try:
                resp = requests.post(url, headers=ms_headers, json=[str(ref)], timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    obj_list = data.get("object")
                    if obj_list and len(obj_list) > 0:
                        consignment = obj_list[0]
                        c_total = consignment.get("consignmentTotal", {})
                        
                        cost = c_total.get("totalCostPrice") or c_total.get("
