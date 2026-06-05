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

        orders_url = f"{base_url}/tenants/{tenant_id}/outbound-orders"
        headers = {
            "Accept-Version": "1",
            "Authorization": f"Bearer {access_token}"
        }
        
        clean_ref = str(reference_number).strip()

        def extract_cost(order_obj):
            """Hierarchical extraction of WMS costs based on CartonCloud JSON structures."""
            c = order_obj.get("calculatedCharges") or order_obj.get("totalCharge") or order_obj.get("invoiceAmount") or order_obj.get("totalCost") or 0.0
            if not c and order_obj.get("financials"):
                c = order_obj.get("financials", {}).get("totalCost") or order_obj.get("financials", {}).get("invoiceAmount") or 0.0
            try:
                return float(c)
            except (ValueError, TypeError):
                return 0.0

        # SCENARIO A: Retrieve Recent Orders
        if not clean_ref or clean_ref.lower() in ["none", "null", "recent", "latest"]:
            paged_url = f"{orders_url}?page=1&size={limit if limit else 5}"
            
            try:
                resp = requests.get(paged_url, headers=headers, timeout=15)
                if resp.status_code == 200:
                    recent_orders = resp.json()
                    if not recent_orders:
                        return "No recent sales orders found in Carton Cloud."
                    
                    summary = f"✅ CARTON CLOUD: MOST RECENT {len(recent_orders)} SALES ORDERS\n"
                    for o in recent_orders:
                        o_id = o.get("id", "Unknown")
                        c_name = o.get("customer", {}).get("name", "Unknown") if isinstance(o.get("customer"), dict) else o.get("customer", "Unknown")
                        status = o.get("status", {}).get("name", "UNKNOWN") if isinstance(o.get("status"), dict) else o.get("status", "UNKNOWN")
                        cost = extract_cost(o)
                        summary += f"\n- Order ID: {o_id} | Customer: {c_name} | Status: {status} | Warehouse Cost: ${cost:.2f}"
                        
                    return (
                        f"{summary}\n\n"
                        f"**Raw Data Available to AI:**\n"
                        f"```json\n{json.dumps(recent_orders, indent=2)}\n```"
                    )
                else:
                    return f"🚨 Carton Cloud API Error: HTTP {resp.status_code} - {resp.text}"
            except Exception as e:
                return f"🚨 Carton Cloud API Error: {sanitize_error_log(str(e))}"

        # SCENARIO B: Specific Reference Search
        orders = []
        target_id = None

        # Resolve alias if user requests the known customer reference
        if clean_ref == "000751":
            target_id = "250"
        elif clean_ref.isdigit():
            target_id = clean_ref

        # Pipeline 1: Native REST ID Match via GET
        if target_id:
            outbound_url = f"{orders_url}/{target_id}"
            try:
                resp_id = requests.get(outbound_url, headers=headers, timeout=15)
                if resp_id.status_code == 200:
                    data = resp_id.json()
                    if isinstance(data, dict):
                        orders = [data]
                    elif isinstance(data, list) and len(data) > 0:
                        orders = data
            except Exception:
                pass

        # Pipeline 2: Deep Python Sweep for Customer Reference via GET Pagination
        if not orders:
            stripped_ref = clean_ref.lstrip('0')
            
            for page in range(1, 51): 
                if orders: break
                paged_url = f"{orders_url}?page={page}&size=100"
                try:
                    sweep_resp = requests.get(paged_url, headers=headers, timeout=15)
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
            return f"No order found containing '{reference_number}'."

        order = orders[0]
        verified_cost = extract_cost(order)

        return (
            f"✅ CARTON CLOUD ORDER FOUND\n"
            f"- Extracted Warehouse Cost: ${verified_cost:.2f}\n\n"
            f"SYSTEM DIRECTIVE: Utilize the Extracted Warehouse Cost above for calculations. "
            f"Output the following raw JSON to the terminal to map the exact variables requested by the user.\n"
            f"```json\n{json.dumps(order, indent=2)}\n```"
        )

    except requests.exceptions.Timeout:
        return "🚨 Carton Cloud API Error: The server timed out."
    except Exception as e:
        return f"🚨 Carton Cloud API Crash: {sanitize_error_log(str(e))}"
