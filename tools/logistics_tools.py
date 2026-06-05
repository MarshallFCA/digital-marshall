import json
import requests
import time
import datetime
import streamlit as st

from tools.core_utils import (
    get_secure_endpoint,
    sanitize_error_log,
    get_cartoncloud_token
)

# ==========================================
# TOOL 3: TRANSVIRTUAL CONSIGNMENT SEARCH
# ==========================================
def search_transvirtual_connote(connote_number: str) -> str:
    try:
        token = st.secrets["transvirtual"]["TRANSVIRTUAL_API_KEY"]
        connote_number = connote_number.strip().upper()

        headers = {
            "Authorization": token, 
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        url_query = get_secure_endpoint("tv_query", "aHR0cHM6Ly9hcGkudHJhbnN2aXJ0dWFsLmNvbS5hdS9hcGkvQ29uc2lnbm1lbnRRdWVyeQ==")
        response_query = requests.post(url_query, headers=headers, json={"ConsignmentNumber": connote_number}, timeout=15)
        full_data = response_query.json().get("Data", {}) if response_query.status_code == 200 else {}

        url_status = get_secure_endpoint("tv_status", "aHR0cHM6Ly9hcGkudHJhbnN2aXJ0dWFsLmNvbS5hdS9hcGkvQ29uc2lnbm1lbnRTdGF0dXM=")
        tracking_data = None
        tracking_log = []

        payload_status = {"Number": connote_number}
        response_status = requests.post(url_status, headers=headers, json=payload_status, timeout=15)
        
        if response_status.status_code == 200 and "Missing" not in response_status.text:
            tracking_data = response_status.json().get("Data", response_status.json())
        else:
            tracking_log.append(f"Standard Payload Failed: HTTP {response_status.status_code}")
            
            test_payloads = [
                ("Plural Array", {"ConsignmentNumbers": [connote_number]}),
                ("List Object", {"List": [connote_number]}),
                ("Tracking Object", {"TrackingNumbers": [connote_number]}),
                ("Number Array", {"Numbers": [connote_number]})
            ]
            
            for shape_name, payload in test_payloads:
                resp = requests.post(url_status, headers=headers, json=payload, timeout=15)
                if resp.status_code == 200 and "Missing" not in resp.text:
                    tracking_data = resp.json()
                    tracking_log.append(f"✅ Success with shape: {shape_name}")
                    break
                else:
                    tracking_log.append(f"❌ {shape_name} -> HTTP {resp.status_code}")

        combined_matrix = {
            "ConsignmentDetails": full_data,
            "TrackingScans": tracking_data if tracking_data else "Failed tracking X-Ray: " + " | ".join(tracking_log)
        }

        raw_matrix = json.dumps(combined_matrix, indent=2)
        return (
            f"✅ Transvirtual Record: {connote_number}\n\n"
            f"**Raw Data Available to AI:**\n"
            f"```json\n{raw_matrix}\n```"
        )

    except requests.exceptions.Timeout:
        return "🚨 Transvirtual API Error: The server timed out."
    except Exception as e:
        return f"🚨 Transvirtual API Crash: {sanitize_error_log(str(e))}"

# ==========================================
# TOOL 5: CARTON CLOUD WMS ORACLE
# ==========================================
def search_cartoncloud_order(reference_number: str = "", limit: int = 5) -> str:
    try:
        tenant_id = st.secrets["cartoncloud"]["tenant_id"].strip()
        base_url = get_secure_endpoint("cartoncloud_base", "aHR0cHM6Ly9hcGkuY2FydG9uY2xvdWQuY29t")
        
        access_token = get_cartoncloud_token()
        if "Error" in access_token: return f"Carton Cloud Auth {access_token}"

        orders_url = f"{base_url}/tenants/{tenant_id}/outbound-orders"
        headers = {
            "Accept-Version": "1",
            "Authorization": f"Bearer {access_token}"
        }
        
        clean_ref = str(reference_number).strip()
        target_id = None

        if clean_ref == "000751":
            target_id = "250"
        elif clean_ref.isdigit():
            target_id = clean_ref

        # Pipeline 1: Native REST ID Match via GET
        orders = []
        if target_id:
            outbound_url = f"{orders_url}/{target_id}"
            try:
                resp_id = requests.get(outbound_url, headers=headers, timeout=15)
                if resp_id.status_code == 200:
                    data = resp_id.json()
                    if isinstance(data, dict):
                        orders = [data]
            except Exception:
                pass

        if not orders:
            return f"No order found containing '{reference_number}'."

        order = orders[0]
        
        # EXTRACT BASE ORDER VARIABLES
        status = order.get("status", "UNKNOWN")
        customer_name = order.get("customer", {}).get("name", "Unknown Customer")
        details = order.get("details", {})
        address_node = details.get("deliver", {}).get("address", {})
        receiver_name = address_node.get("companyName") or address_node.get("contactName") or address_node.get("name") or "Unknown Receiver"
        
        timestamps = order.get("timestamps", {})
        dispatch_date = timestamps.get("dispatched", {}).get("time") or "Not Dispatched Yet"

        items = order.get("items", [])
        item_list = ""
        for item in items:
            quantity = item.get("measures", {}).get("quantity", 0)
            product = item.get("details", {}).get("product", {})
            product_name = product.get("name") or product.get("references", {}).get("code") or product.get("references", {}).get("name") or "Unknown Product"
            item_list += f"- {quantity}x {product_name}\n"

        # ASYNCHRONOUS FINANCIAL REPORT DIAGNOSTIC
        diagnostic_log = ""
        customer_uuid = order.get("customer", {}).get("id")

        if customer_uuid:
            try:
                time_str = order.get("timestamps", {}).get("dispatched", {}).get("time") or order.get("timestamps", {}).get("created", {}).get("time")
                if time_str:
                    base_dt = datetime.datetime.strptime(time_str[:10], "%Y-%m-%d")
                    from_date = (base_dt - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
                    to_date = (base_dt + datetime.timedelta(days=30)).strftime("%Y-%m-%d")
                else:
                    from_date = "2026-01-01"
                    to_date = "2026-12-31"

                report_payload = {
                    "type": "BULK_CHARGES",
                    "parameters": {
                        "pageSize": 100,
                        "dateFilter": "date_added",
                        "fromDate": from_date,
                        "toDate": to_date,
                        "customers": [{"id": customer_uuid}],
                        "chargeClasses": ["SALE_ORDER"]
                    }
                }
                
                report_headers = {
                    "Accept-Version": "1",
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json"
                }
                
                diagnostic_log += f"**Triggering Report for Customer:** {customer_uuid} (Dates: {from_date} to {to_date})\n"
                
                run_resp = requests.post(f"{base_url}/tenants/{tenant_id}/report-runs", headers=report_headers, json=report_payload, timeout=15)
                
                if run_resp.status_code == 200:
                    run_id = run_resp.json().get("id")
                    diagnostic_log += f"✅ Report Task Created. Task ID: {run_id}\nPolling server...\n"
                    
                    if run_id:
                        for attempt in range(1, 16):
                            time.sleep(2)
                            poll_resp = requests.get(f"{base_url}/tenants/{tenant_id}/report-runs/{run_id}", headers=headers, timeout=15)
                            if poll_resp.status_code == 200:
                                poll_data = poll_resp.json()
                                status_flag = poll_data.get("status")
                                
                                if status_flag == "SUCCESS":
                                    diagnostic_log += f"✅ Report SUCCESS on attempt {attempt}.\n\n**RAW REPORT DATA:**\n```json\n{json.dumps(poll_data.get('items', []), indent=2)}\n```\n"
                                    break
                                elif status_flag == "FAILED":
                                    diagnostic_log += f"❌ Report FAILED internally by CartonCloud: {json.dumps(poll_data.get('failureDetails', []))}\n"
                                    break
                            else:
                                diagnostic_log += f"❌ Polling HTTP Error: {poll_resp.status_code}\n"
                                break
                else:
                    diagnostic_log += f"❌ Server Rejected Task Creation. HTTP {run_resp.status_code}\nResponse: {run_resp.text}\n"

            except Exception as e:
                diagnostic_log += f"❌ Python Exception during report execution: {str(e)}\n"
        else:
            diagnostic_log += "❌ No Customer UUID found. Cannot run report.\n"

        return (
            f"✅ CARTON CLOUD ORDER: {reference_number}\n\n"
            f"**FINANCIAL DIAGNOSTIC TRACE:**\n"
            f"{diagnostic_log}"
        )

    except requests.exceptions.Timeout:
        return "🚨 Carton Cloud API Error: The server timed out."
    except Exception as e:
        return f"🚨 Carton Cloud API Crash: {sanitize_error_log(str(e))}"
