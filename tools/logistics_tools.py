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
        return f"✅ Transvirtual Record: {connote_number}\n\n**Raw Data Available to AI:**\n```json\n{raw_matrix}\n```"

    except requests.exceptions.Timeout:
        return "🚨 Transvirtual API Error: The server timed out."
    except Exception as e:
        return f"🚨 Transvirtual API Crash: {sanitize_error_log(str(e))}"

# ==========================================
# TOOL 5: CARTON CLOUD WMS ORACLE
# ==========================================
def search_cartoncloud_order(reference_number: str) -> str:
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
        orders = []

        # Pipeline 1: Native API Search across multiple reference pointers
        reference_pointers = [
            "/references/customer",
            "/salesOrderReference",
            "/warehouseReference"
        ]
        
        for pointer in reference_pointers:
            if orders: break
            search_payload = {
                "condition": {
                    "type": "TextComparisonCondition",
                    "field": { "type": "JsonField", "pointer": pointer },
                    "value": { "type": "ValueField", "value": clean_ref },
                    "method": "CONTAINS"
                }
            }
            try:
                resp = requests.post(search_url, headers=headers, json=search_payload, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    if data:
                        orders = data
            except Exception:
                pass

        # Pipeline 2: Native ID Match (Handling potential leading zeros)
        if not orders and clean_ref.isdigit():
            id_payload = {
                "condition": {
                    "type": "EqualsCondition",
                    "field": { "type": "JsonField", "pointer": "/id" },
                    "value": { "type": "ValueField", "value": int(clean_ref) }
                }
            }
            try:
                resp_id = requests.post(search_url, headers=headers, json=id_payload, timeout=10)
                if resp_id.status_code == 200:
                    data = resp_id.json()
                    if data:
                        orders = data
            except Exception:
                pass

        # Pipeline 3: Deep Python Sweep for Historical Anomalies
        if not orders:
            stripped_ref = clean_ref.lstrip('0')
            brute_payload = {
                "sort": [{"field": {"type": "JsonField", "pointer": "/id"}, "direction": "DESC"}],
                "page": 1,
                "size": 500  # Expanded to 500 records per page to cover months of history natively
            }
            
            for page in range(1, 11): # Deep sweep: 5,000 records
                if orders: break
                brute_payload["page"] = page
                try:
                    sweep_resp = requests.post(search_url, headers=headers, json=brute_payload, timeout=15)
                    if sweep_resp.status_code == 200:
                        page_data = sweep_resp.json()
                        if not page_data: break # Break loop if we run out of historical pages
                        
                        for o in page_data:
                            order_id = str(o.get("id", ""))
                            cust_ref = str(o.get("references", {}).get("customer", ""))
                            sales_ref = str(o.get("salesOrderReference", ""))
                            wh_ref = str(o.get("warehouseReference", ""))
                            
                            # Match exact, or substring, or stripped integer equivalent to bypass formatting errors
                            if clean_ref in [order_id, cust_ref, sales_ref, wh_ref] or \
                               clean_ref in cust_ref or clean_ref in sales_ref or \
                               (stripped_ref and stripped_ref in [order_id, cust_ref, sales_ref, wh_ref]):
                                orders = [o]
                                break
                except Exception:
                    break

        if not orders:
            return f"No order found in Carton Cloud containing reference or ID: {reference_number}. Ensure the record is within the last 5,000 dispatches."

        order = orders[0]
        status = order.get("status", "UNKNOWN")
        customer_name = order.get("customer", {}).get("name", "Unknown Customer")
        
        details = order.get("details", {})
        address_node = details.get("deliver", {}).get("address", {})
        receiver_name = (
            address_node.get("companyName") or 
            address_node.get("contactName") or 
            address_node.get("name") or 
            "Unknown Receiver"
        )

        timestamps = order.get("timestamps", {})
        dispatch_date = timestamps.get("dispatched", {}).get("time") or "Not Dispatched Yet"
        
        # Financial Extraction logic
        financials = order.get("financials", {})
        warehouse_cost = financials.get("totalCost") or financials.get("invoiceAmount") or order.get("totalCost") or order.get("calculatedCharges", 0.0)

        items = order.get("items", [])
        item_list = ""
        
        for item in items:
            quantity = item.get("measures", {}).get("quantity", 0)
            product = item.get("details", {}).get("product", {})
            product_name = product.get("name") or product.get("references", {}).get("code") or product.get("references", {}).get("name") or "Unknown Product"
            item_list += f"- {quantity}x {product_name}\n"

        return f"""
        ✅ CARTON CLOUD ORDER FOUND
        - Reference/ID: {reference_number}
        - Status: {status}
        - Customer: {customer_name}
        - Receiver: {receiver_name}
        - Dispatch Date: {dispatch_date}
        - Warehouse Cost: ${float(warehouse_cost):.2f}
        
        Items in this order:
        {item_list if item_list else "No items listed."}
        """

    except requests.exceptions.Timeout:
        return "🚨 Carton Cloud API Error: The server timed out."
    except Exception as e:
        return f"🚨 Carton Cloud API Error: {sanitize_error_log(str(e))}"
