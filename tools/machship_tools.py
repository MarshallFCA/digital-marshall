import requests
import json
import re
import io
import pandas as pd
import streamlit as st
from tools.core_utils import get_secure_endpoint, sanitize_error_log

# ==========================================
# TOOL 2: UNRESTRICTED MACHSHIP SEARCH
# ==========================================
def search_machship_connote(connote_number: str) -> str:
    token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
    connote_number = connote_number.strip().upper()
    headers = { "token": token, "Accept": "application/json" }

    try:
        if connote_number.startswith("MS"):
            ms_id = re.sub(r"\D", "", connote_number)
            base_url = get_secure_endpoint("machship_get", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvZ2V0Q29uc2lnbm1lbnQ/aWQ9")
            url = f"{base_url}{ms_id}"
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("object"):
                    consignment = data["object"]
                    carrier = consignment.get("carrier", {}).get("name") or consignment.get("carrier", {}).get("abbreviation") or "Carrier Not Assigned"
                    status = consignment.get("status", {}).get("name", "Unknown Status")
                    
                    raw_data = json.dumps(consignment, indent=2)
                    return f"✅ Machship Record (MS): Carrier: {carrier} | Status: {status}\n\n**Raw Data Available to AI:**\n```json\n{raw_data}\n```"

        headers["Content-Type"] = "application/json"
        search_routes = [
            ("Carrier ID", get_secure_endpoint("machship_carrier_id", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlDYXJyaWVyQ29uc2lnbm1lbnRJZD9pbmNsdWRlQ2hpbGRDb21wYW5pZXM9dHJ1ZQ==")),
            ("Reference 1", get_secure_endpoint("machship_ref1", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UxP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl")),
            ("Reference 2", get_secure_endpoint("machship_ref2", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UyP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl"))
        ]
        payload = [connote_number]

        for search_type, url in search_routes:
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            if response.status_code == 200:
                data = response.json()
                if data.get("object") and len(data["object"]) > 0:
                    consignment = data["object"][0]
                    carrier = consignment.get("carrier", {}).get("name") or consignment.get("carrier", {}).get("abbreviation") or "Carrier Not Assigned"
                    status = consignment.get("status", {}).get("name", "Unknown Status")
                    
                    raw_data = json.dumps(consignment, indent=2)
                    return f"✅ Machship Record (Found via {search_type}): Carrier: {carrier} | Status: {status}\n\n**Raw Data Available to AI:**\n```json\n{raw_data}\n```"

        return f"Failed to find '{connote_number}' in Machship."
    except requests.exceptions.Timeout:
        return "🚨 Machship API Error: The server timed out."
    except Exception as e:
        return f"🚨 Machship API Error: {sanitize_error_log(str(e))}"

# ==========================================
# TOOL 6: MASS MATRIX PROCESSOR
# ==========================================
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_australian_postcodes():
    import csv
    url = get_secure_endpoint("aus_postcodes", "aHR0cHM6Ly9yYXcuZ2l0aHVidXNlcmNvbnRlbnQuY29tL21hdHRoZXdwcm9jdG9yL2F1c3RyYWxpYW5wb3N0Y29kZXMvbWFzdGVyL2F1c3RyYWxpYW5fcG9zdGNvZGVzLmNzdg==")
    pc_to_suburb = {}
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            lines = resp.text.splitlines()
            reader = csv.DictReader(lines)
            for row in reader:
                pc = row.get('postcode')
                loc = row.get('locality')
                if pc and loc and pc not in pc_to_suburb:
                    pc_to_suburb[pc] = loc.upper()
    except:
        pass
    return pc_to_suburb.copy()

def generate_bulk_matrix(file_bytes, margin_target, excluded_carriers):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime, timedelta

    try:
        try:
            df = pd.read_csv(io.BytesIO(file_bytes))
        except UnicodeDecodeError:
            df = pd.read_csv(io.BytesIO(file_bytes), encoding='cp1252', encoding_errors='replace')
            
        pc_db = fetch_australian_postcodes()
        
        def get_val(row_s, possible_cols, default=""):
            for col in possible_cols:
                if col in row_s and pd.notna(row_s[col]):
                    return str(row_s[col]).strip()
            return default
            
        next_day = datetime.now() + timedelta(days=1)
        while next_day.weekday() >= 5:  
            next_day += timedelta(days=1)
        dispatch_date = next_day.strftime("%Y-%m-%dT09:00:00")

        token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
        url = get_secure_endpoint("machship_routes", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9yb3V0ZXMvcmV0dXJucm91dGVz")
        headers = {"token": token, "Content-Type": "application/json"}
        company_id = 53031 

        def fetch_route(index, row):
            to_sub = get_val(row, ["Destination", "To Suburb", "To", "Suburb"], "")
            to_post = get_val(row, ["To PC", "Postcode"], "").replace(".0", "")
            
            from_sub = get_val(row, ["From", "From Suburb", "Origin"], "Seaford")
            from_post = get_val(row, ["From PC", "Origin Postcode"], "3198").replace(".0", "")
            
            if len(from_sub) <= 4 and from_post in pc_db:
                from_sub = pc_db[from_post]
            if len(to_sub) <= 4 and to_post in pc_db:
                to_sub = pc_db[to_post]

            qty_items = float(get_val(row, ["Items"], 0))
            qty_pallets = float(get_val(row, ["Pallets"], 0))
            weight = float(get_val(row, ["KGS", "Weight", "Total Weight", "Charged KGs"], 0))
            cubic = float(get_val(row, ["Cubic", "Volume"], 0))

            if qty_pallets > 0:
                qty = int(qty_pallets)
                item_name = "Pallet"
            elif qty_items > 0:
                qty = int(qty_items)
                item_name = "Carton"
            else:
                qty = 1
                item_name = "Item"

            if qty <= 0: qty = 1
            weight_per_item = weight / qty if weight > 0 else 1.0
            cubic_per_item = cubic / qty if cubic > 0 else 0.001
            
            side_m = cubic_per_item ** (1/3)
            side_cm = int(side_m * 100)
            if side_cm < 1: side_cm = 10

            payload = {
                "companyId": company_id,
                "fromLocation": {"suburb": from_sub, "postcode": from_post},
                "toLocation": {"suburb": to_sub, "postcode": to_post},
                "items": [{
                    "itemType": "Item", 
                    "name": item_name,
                    "quantity": qty, 
                    "weight": weight_per_item,
                    "length": side_cm, "width": side_cm, "height": side_cm 
                }],
                "despatchDateTimeLocal": dispatch_date
            }

            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=15)
                if resp.status_code != 200:
                    return index, "API Error", []

                data = resp.json()
                routes = data.get('object', {}).get('routes', [])
                
                valid_routes = []
                for r in routes:
                    raw_carrier_name = r.get('carrier', {}).get('name', 'Unknown')
                    
                    if any(ex.lower() in raw_carrier_name.lower() for ex in excluded_carriers):
                        continue
                        
                    acc_node = r.get('companyCarrierAccount') or r.get('carrierAccount') or {}
                    acc_name = acc_node.get('name') or acc_node.get('accountCode') or ''
                    
                    service_name = r.get('companyCarrierAccountService', {}).get('name') or r.get('carrierService', {}).get('name') or ''
                    
                    display_name = raw_carrier_name
                    if service_name: 
                        display_name += f" - {service_name}"
                    if acc_name: 
                        display_name += f" [{acc_name}]"

                    c_total = r.get('consignmentTotal') or {}
                    
                    base_cost = c_total.get('totalCost')
                    if base_cost is not None:
                        sell_price = float(base_cost) / (1 - (margin_target / 100))
                    else:
                        sell_price = c_total.get('totalSellPrice')

                    if sell_price is not None:
                        valid_routes.append({
                            'raw_carrier': raw_carrier_name,
                            'display': display_name,
                            'price': float(sell_price)
                        })

                if valid_routes:
                    valid_routes.sort(key=lambda x: x['price'])
                    unique_options = []
                    seen_carriers = set()
                    for vr in valid_routes:
                        if vr['raw_carrier'] not in seen_carriers:
                            seen_carriers.add(vr['raw_carrier'])
                            unique_options.append(vr)
                        if len(unique_options) == 3:
                            break
                    
                    return index, "Success", unique_options
                    
                return index, "No Valid Routes", []
                
            except Exception as e:
                return index, f"Crash: {sanitize_error_log(str(e))}", []

        with ThreadPoolExecutor(max_workers=15) as executor:
            future_to_row = {executor.submit(fetch_route, index, row): index for index, row in df.iterrows()}
            
            for future in as_completed(future_to_row):
                idx, status, options = future.result()
                
                if status != "Success":
                    df.at[idx, "Routing Status"] = status
                else:
                    df.at[idx, "Routing Status"] = "Success"
                    if len(options) > 0:
                        df.at[idx, "Option 1 (Cheapest)"] = options[0]['display']
                        df.at[idx, "Option 1 Price"] = f"${options[0]['price']:.2f}"
                    if len(options) > 1:
                        df.at[idx, "Option 2 (Alternative)"] = options[2]['display']
                        df.at[idx, "Option 2 Price"] = f"${options[2]['price']:.2f}"
                    if len(options) > 2:
                        df.at[idx, "Option 3 (Alternative)"] = options[3]['display']
                        df.at[idx, "Option 3 Price"] = f"${options[2]['price']:.2f}"

        return True, df

    except Exception as e:
        return False, f"Matrix Engine Crash: {sanitize_error_log(str(e))}"
