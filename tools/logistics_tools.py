import json
import requests
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

        search_url = f"{base_url}/tenants/{tenant_id}/outbound-orders/search"
        headers = {
            "Accept-Version": "1",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        clean_ref = str(reference_number).strip()

        # SCENARIO A: Retrieve Recent Orders
        if not clean_ref or clean_ref.lower() in ["none", "null", "recent", "latest"]:
            paged_url = f"{search_url}?page=1&size={limit if limit else 5}"
            brute_payload = {
                "sort": [{"field": {"type": "JsonField", "pointer": "/id"}, "direction": "DESC"}]
            }
            try:
                resp = requests.post(paged_url, headers=headers, json=brute_payload, timeout=15)
                if resp.status_code == 200:
                    recent_orders = resp.json()
                    if not recent_orders:
                        return "No recent sales orders found in Carton Cloud."
                    return (
                        f"✅ CARTON CLOUD: MOST RECENT {len(recent_orders)} SALES ORDERS\n\n"
                        f"**Raw Data Available to AI:**\n"
                        f"```json\n{json.dumps(recent_orders, indent=2)}\n```"
                    )
                else:
                    return f"🚨 Carton Cloud API Error: HTTP {resp.status_code} - {resp.text}"
            except Exception as e:
                return f"🚨 Carton Cloud API Error: {sanitize_error_log(str(e))}"

        # SCENARIO B: Specific Reference Search
        orders = []

        # Pipeline 1: Native ID Match
        if clean_ref.isdigit():
            id_payload = {
                "condition": {
                    "type": "EqualsCondition",
                    "field": { "type": "JsonField", "pointer": "/id" },
                    "value": { "type": "ValueField", "value": int(clean_ref) }
                }
            }
            try:
                resp_id = requests.post(f"{search_url}?page=1&size=1", headers=headers, json=id_payload, timeout=10)
                if resp_id.status_code == 200:
                    data = resp_id.json()
                    if data: orders = data
            except Exception:
                pass

        # Pipeline 2: Deep Python Sweep for Historical Anomalies (API Safe Pagination + Global JSON Match)
        if not orders:
            stripped_ref = clean_ref.lstrip('0')
            brute_payload = {
                "sort": [{"field": {"type": "JsonField", "pointer": "/id"}, "direction": "DESC"}]
            }
            
            for page in range(1, 11): 
                if orders: break
                paged_url = f"{search_url}?page={page}&size=100"
                try:
                    sweep_resp = requests.post(paged_url, headers=headers, json=brute_payload, timeout=15)
                    if sweep_resp.status_code == 200:
                        page_data = sweep_resp.json()
                        if not page_data: break 
                        
                        for o in page_data:
                            raw_o_str = json.dumps(o)
                            if clean_ref in raw_o_str or (stripped_ref and stripped_ref in raw_o_str):
                                orders = [o]
                                break
                    else:
                        break 
                except Exception:
                    break

        if not orders:
            diag_url = f"{search_url}?page=1&size=1"
            diag_resp = requests.post(diag_url, headers=headers, json={"sort": [{"field": {"type": "JsonField", "pointer": "/id"}, "direction": "DESC"}]}, timeout=15)
            diag_json = diag_resp.json() if diag_resp.status_code == 200 else []
            return (
                f"No order found containing '{reference_number}'.\n\n"
                f"**SCHEMA DIAGNOSTIC (Latest Order in DB):**\n"
                f"```json\n{json.dumps(diag_json, indent=2)}\n```"
            )

        order = orders[0]
        return (
            f"✅ CARTON CLOUD ORDER FOUND: {reference_number}\n\n"
            f"**Raw Data Available to AI:**\n"
            f"```json\n{json.dumps(order, indent=2)}\n```"
        )

    except requests.exceptions.Timeout:
        return "🚨 Carton Cloud API Error: The server timed out."
    except Exception as e:
        return f"🚨 Carton Cloud API Error: {sanitize_error_log(str(e))}"
