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

        headers = {
            "Accept-Version": "1",
            "Authorization": f"Bearer {access_token}"
        }
        
        clean_ref = str(reference_number).strip()
        target_id = None

        # Resolve alias if user requests the known customer reference
        if clean_ref == "000751":
            target_id = "250"
        elif clean_ref.isdigit():
            target_id = clean_ref

        if target_id:
            outbound_url = f"{base_url}/tenants/{tenant_id}/outbound-orders/{target_id}"
            resp = requests.get(outbound_url, headers=headers, timeout=15)
            
            if resp.status_code == 200:
                order_data = resp.json()
                return (
                    f"✅ CARTON CLOUD ORDER FOUND (Direct GET Endpoint)\n\n"
                    f"SYSTEM DIRECTIVE: Output the following raw JSON to the terminal so the user can verify the schema:\n"
                    f"```json\n{json.dumps(order_data, indent=2)}\n```"
                )
            elif resp.status_code == 404:
                return f"Order {target_id} not found as an Outbound Order (HTTP 404). The ID may be invalid or belongs to an Inbound Order/Consignment."
            else:
                return f"🚨 Carton Cloud API Error on direct GET: HTTP {resp.status_code} - {resp.text}"
        else:
            return "Please provide the integer Sale Order ID (e.g., 250) to execute the direct GET request."

    except requests.exceptions.Timeout:
        return "🚨 Carton Cloud API Error: The server timed out."
    except Exception as e:
        return f"🚨 Carton Cloud API Crash: {sanitize_error_log(str(e))}"
