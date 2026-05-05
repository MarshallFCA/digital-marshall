import requests
import base64
import re
import json
import io
import pandas as pd
import numpy as np
import datetime
import PyPDF2
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ==========================================
# OWASP TELEMETRY SANITIZER (DSGAI14)
# ==========================================
def sanitize_error_log(error_msg: str) -> str:
    """
    Strips sensitive tokens, OAuth keys, and PII from error traces 
    before they are printed to logs or the UI.
    """
    msg = str(error_msg)
    # Redact Bearer tokens
    msg = re.sub(r'(?i)Bearer\s+[A-Za-z0-9\-\._~]+', 'Bearer [REDACTED_TOKEN]', msg)
    # Redact generic token parameters
    msg = re.sub(r'(?i)token=[A-Za-z0-9\-\._~]+', 'token=[REDACTED_TOKEN]', msg)
    msg = re.sub(r'(?i)api_key=[A-Za-z0-9\-\._~]+', 'api_key=[REDACTED_KEY]', msg)
    # Redact emails to prevent PII leakage
    msg = re.sub(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', '[EMAIL_REDACTED]', msg)
    return msg

# ==========================================
# AUTHENTICATION CACHES
# ==========================================
@st.cache_data(ttl=1800, show_spinner=False)
def get_xero_token():
    try:
        client_id = st.secrets["xero"]["XERO_CLIENT_ID"]
        client_secret = st.secrets["xero"]["XERO_CLIENT_SECRET"]
        credentials = f"{client_id}:{client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        
        url = base64.b64decode("aHR0cHM6Ly9pZGVudGl0eS54ZXJvLmNvbS9jb25uZWN0L3Rva2Vu").decode()
        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = { "grant_type": "client_credentials" }
        
        response = requests.post(url, headers=headers, data=data, timeout=15)
        response.raise_for_status()
        return response.json()["access_token"]
    except Exception as e:
        return f"Error: {sanitize_error_log(str(e))}"

@st.cache_data(ttl=3000, show_spinner=False)
def get_cartoncloud_token():
    try:
        client_id = st.secrets["cartoncloud"]["client_id"].strip()
        client_secret = st.secrets["cartoncloud"]["client_secret"].strip()
        base_url = base64.b64decode("aHR0cHM6Ly9hcGkuY2FydG9uY2xvdWQuY29t").decode()

        credentials = f"{client_id}:{client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('utf-8')
        auth_url = f"{base_url}/uaa/oauth/token"
        auth_headers = {
            "Accept-Version": "1",
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {encoded_credentials}"
        }
        
        response = requests.post(auth_url, data="grant_type=client_credentials", headers=auth_headers, timeout=15)
        response.raise_for_status()
        return response.json().get("access_token")
    except Exception as e:
        return f"Error: {sanitize_error_log(str(e))}"

# ==========================================
# TOOL 1: XERO FINANCIAL SEARCH
# ==========================================
def search_xero_contact(contact_name: str) -> str:
    token = get_xero_token()
    if "Error" in token: return f"Xero Auth {token}" 
    
    headers = { "Authorization": f"Bearer {token}", "Accept": "application/json" }
    
    def fetch_contacts(search_term):
        safe_name = requests.utils.quote(search_term)
        base_url = base64.b64decode("aHR0cHM6Ly9hcGkueGVyby5jb20vYXBpLnhyby8yLjAvQ29udGFjdHM/d2hlcmU9TmFtZS5Db250YWlucygi").decode()
        url = f'{base_url}{safe_name}")'
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json().get("Contacts", [])

    try:
        contacts = fetch_contacts(contact_name)
        
        if not contacts and " " in contact_name:
            first_word = contact_name.split()[0]
            if len(first_word) > 2:
                contacts = fetch_contacts(first_word)
        
        if contacts:
            results_summary = []
            for contact in contacts[:3]:
                name = contact.get("Name", "Unknown")
                status = contact.get("ContactStatus", "Unknown")
                balances = contact.get("Balances", {}).get("AccountsReceivable", {})
                outstanding = balances.get("Outstanding", 0.00)
                overdue = balances.get("Overdue", 0.00)
                results_summary.append(f"✅ Xero Record: {name} | Status: {status} | Outstanding: ${outstanding} | Overdue: ${overdue}")
            
            raw_data = json.dumps(contacts[:3], indent=2)
            summary_string = "\n".join(results_summary)
            return f"{summary_string}\n\n**Raw Data Available to AI:**\n```json\n{raw_data}\n```"
        else:
            return f"No contact found in Xero matching '{contact_name}' or its primary keyword."
            
    except Exception as e:
        return f"🚨 Xero API Error: {sanitize_error_log(str(e))}"

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
            base_url = base64.b64decode("aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvZ2V0Q29uc2lnbm1lbnQ/aWQ9").decode()
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
            ("Carrier ID", base64.b64decode("aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlDYXJyaWVyQ29uc2lnbm1lbnRJZD9pbmNsdWRlQ2hpbGRDb21wYW5pZXM9dHJ1ZQ==").decode()),
            ("Reference 1", base64.b64decode("aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UxP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl").decode()),
            ("Reference 2", base64.b64decode("aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UyP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl").decode())
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
# TOOL 3: TRANSVIRTUAL CONSIGNMENT SEARCH
# ==========================================
def search_transvirtual_connote(connote_number: str) -> str:
    import json
    import requests
    import streamlit as st

    try:
        token = st.secrets["transvirtual"]["TRANSVIRTUAL_API_KEY"]
        connote_number = connote_number.strip().upper()

        headers = {
            "Authorization": token, 
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        url_query = base64.b64decode("aHR0cHM6Ly9hcGkudHJhbnN2aXJ0dWFsLmNvbS5hdS9hcGkvQ29uc2lnbm1lbnRRdWVyeQ==").decode()
        response_query = requests.post(url_query, headers=headers, json={"ConsignmentNumber": connote_number}, timeout=15)
        full_data = response_query.json().get("Data", {}) if response_query.status_code == 200 else {}

        url_status = base64.b64decode("aHR0cHM6Ly9hcGkudHJhbnN2aXJ0dWFsLmNvbS5hdS9hcGkvQ29uc2lnbm1lbnRTdGF0dXM=").decode()
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
# TOOL 4: GOOGLE DRIVE ORACLE
# ==========================================
def search_and_read_google_drive(search_query: str) -> str:
    try:
        drive_ro_scope = base64.b64decode("aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9kcml2ZS5yZWFkb25seQ==").decode()
        
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=[drive_ro_scope]
        )
        service = build('drive', 'v3', credentials=creds)

        safe_query = search_query.replace("'", "\\'")
        query = f"fullText contains '{safe_query}' or name contains '{safe_query}'"
        
        results = service.files().list(
            q=query,
            pageSize=3,
            orderBy="modifiedTime desc",
            fields="nextPageToken, files(id, name, mimeType)"
        ).execute()
        
        items = results.get('files', [])
        
        if not items:
            return f"No documents found in Google Drive matching: '{search_query}'."
            
        file = items[0]
        file_id = file['id']
        file_name = file['name']
        mime_type = file['mimeType']
        content = ""
        
        if 'application/vnd.google-apps.document' in mime_type:
            request = service.files().export_media(fileId=file_id, mimeType='text/plain')
            content = request.execute().decode('utf-8')
            
        elif 'application/vnd.google-apps.spreadsheet' in mime_type:
            request = service.files().export_media(fileId=file_id, mimeType='text/csv')
            content = request.execute().decode('utf-8')
            
        elif 'spreadsheetml.sheet' in mime_type or 'application/vnd.ms-excel' in mime_type:
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO(request.execute())
            df = pd.read_excel(fh)
            content = df.to_csv(index=False)
            
        elif 'application/pdf' in mime_type:
            request = service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
            fh.seek(0)
            pdf_reader = PyPDF2.PdfReader(fh)
            for page in pdf_reader.pages:
                if page.extract_text():
                    content += page.extract_text() + "\n"
                
        elif 'text/plain' in mime_type or 'text/csv' in mime_type:
            request = service.files().get_media(fileId=file_id)
            content = request.execute().decode('utf-8')
            
        else:
            return f"Found '{file_name}', but it is an unsupported format ({mime_type})."

        max_chars = 15000
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... [TRUNCATED DUE TO LENGTH: Data exceeds AI memory limit.]"

        return f"✅ GOOGLE DRIVE MATCH FOUND: '{file_name}'\n\n**Document Content:**\n{content}"
    except Exception as e:
        return f"🚨 Google Drive Connection Crash: {sanitize_error_log(str(e))}"
    finally:
        try:
            del creds, service
        except NameError:
            pass

# ==========================================
# TOOL 5: CARTON CLOUD WMS ORACLE
# ==========================================
def search_cartoncloud_order(reference_number: str) -> str:
    try:
        tenant_id = st.secrets["cartoncloud"]["tenant_id"].strip()
        base_url = base64.b64decode("aHR0cHM6Ly9hcGkuY2FydG9uY2xvdWQuY29t").decode()
        
        access_token = get_cartoncloud_token()
        if "Error" in access_token: return f"Carton Cloud Auth {access_token}"

        search_url = f"{base_url}/tenants/{tenant_id}/outbound-orders/search"
        headers = {
            "Accept-Version": "1",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        search_payload = {
            "condition": {
                "type": "AndCondition",
                "conditions": [
                    {
                        "type": "TextComparisonCondition",
                        "field": {
                            "type": "JsonField",
                            "pointer": "/references/customer"
                        },
                        "value": {
                            "type": "ValueField",
                            "value": str(reference_number)
                        },
                        "method": "CONTAINS"
                    }
                ]
            }
        }

        response = requests.post(search_url, headers=headers, json=search_payload, timeout=15)
        response.raise_for_status()
        orders = response.json()

        if not orders:
            return f"No order found in Carton Cloud containing reference: {reference_number}."

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

        items = order.get("items", [])
        item_list = ""
        
        for item in items:
            quantity = item.get("measures", {}).get("quantity", 0)
            product = item.get("details", {}).get("product", {})
            product_name = product.get("name") or product.get("references", {}).get("code") or product.get("references", {}).get("name") or "Unknown Product"
            item_list += f"- {quantity}x {product_name}\n"

        return f"""
        ✅ CARTON CLOUD ORDER FOUND
        - Reference: {reference_number}
        - Status: {status}
        - Customer: {customer_name}
        - Receiver: {receiver_name}
        - Dispatch Date: {dispatch_date}
        
        Items in this order:
        {item_list if item_list else "No items listed."}
        """

    except requests.exceptions.Timeout:
        return "🚨 Carton Cloud API Error: The server timed out."
    except Exception as e:
        return f"🚨 Carton Cloud API Error: {sanitize_error_log(str(e))}"

# ==========================================
# TOOL 6: MASS MATRIX PROCESSOR
# ==========================================
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_australian_postcodes():
    import requests
    import csv
    url = base64.b64decode("aHR0cHM6Ly9yYXcuZ2l0aHVidXNlcmNvbnRlbnQuY29tL21hdHRoZXdwcm9jdG9yL2F1c3RyYWxpYW5wb3N0Y29kZXMvbWFzdGVyL2F1c3RyYWxpYW5fcG9zdGNvZGVzLmNzdg==").decode()
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
    return pc_to_suburb

def generate_bulk_matrix(file_bytes, margin_target, excluded_carriers):
    import pandas as pd
    import io
    import requests
    import streamlit as st
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
        url = base64.b64decode("aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9yb3V0ZXMvcmV0dXJucm91dGVz").decode()
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

        from concurrent.futures import ThreadPoolExecutor, as_completed
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
                        df.at[idx, "Option 2 (Alternative)"] = options[1]['display']
                        df.at[idx, "Option 2 Price"] = f"${options[1]['price']:.2f}"
                    if len(options) > 2:
                        df.at[idx, "Option 3 (Alternative)"] = options[2]['display']
                        df.at[idx, "Option 3 Price"] = f"${options[2]['price']:.2f}"

        return True, df

    except Exception as e:
        return False, f"Matrix Engine Crash: {sanitize_error_log(str(e))}"

# ==========================================
# TOOL 7: PANDAS ORCHESTRATOR
# ==========================================
def hybrid_gemini_sheet_generator(instructions: str, target_sheet_name: str) -> str:
    import google.generativeai as genai
    import pandas as pd
    import numpy as np
    import datetime
    import re
    import io
    import json
    import streamlit as st
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    try:
        uploaded_files = st.session_state.get("chat_uploader")
        if not uploaded_files:
            return "Error: No files currently uploaded in the Oracle Data Ingestion port. Please upload payloads first."

        df_list = []
        for uf in uploaded_files:
            file_extension = uf.name.split(".")[-1].lower()
            uf.seek(0)
            try:
                if file_extension == "csv":
                    try:
                        temp_df = pd.read_csv(uf)
                    except UnicodeDecodeError:
                        uf.seek(0)
                        temp_df = pd.read_csv(uf, encoding='cp1252', encoding_errors='replace')
                elif file_extension in ["xlsx", "xls"]:
                    temp_df = pd.read_excel(uf)
                else:
                    continue
                df_list.append(temp_df)
            except Exception as read_err:
                return f"Error reading file {uf.name}: {sanitize_error_log(str(read_err))}"

        if not df_list:
            return "Error: No valid CSV or Excel files were found to combine."

        main_df = pd.concat(df_list, ignore_index=True)

        gemini_key = st.secrets.get("GEMINI_API_KEY")
        if not gemini_key:
            return "Error: GEMINI_API_KEY is missing from the telemetry secrets."

        genai.configure(api_key=gemini_key)
        
        try:
            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            target_model = None
            preferred = ['models/gemini-1.5-pro', 'models/gemini-1.5-pro-latest', 'models/gemini-1.5-flash', 'models/gemini-1.5-flash-latest', 'models/gemini-pro']
            
            for pref in preferred:
                if pref in available_models:
                    target_model = pref
                    break
                    
            if not target_model:
                for m in available_models:
                    if 'gemini-1.5-pro' in m:
                        target_model = m
                        break
            
            if not target_model and available_models:
                target_model = available_models[0]
                
            if not target_model:
                return "Error: No valid text generation models found for this API key."
                
            target_model = target_model.replace('models/', '')
            model = genai.GenerativeModel(target_model)
        except Exception as model_err:
            return f"HYBRID GEMINI CRASH (Model Auto-Detect Failed): {sanitize_error_log(str(model_err))}"

        schema_info = main_df.dtypes.to_string()

        # ==========================================
        # OWASP CONTEXT MINIMIZER (DSGAI15)
        # ==========================================
        # We mask PII in the 3-row sample before sending it to the LLM. 
        # The actual massive dataset is processed locally by pandas, so this doesn't break the final output.
        sample_df = main_df.head(3).copy()
        for col in sample_df.columns:
            if sample_df[col].dtype == 'object':
                # Mask emails to prevent inadvertent leakage
                sample_df[col] = sample_df[col].astype(str).apply(lambda x: re.sub(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', '[EMAIL_MASKED]', x))
        sample_data = sample_df.to_csv(index=False)

        prompt = f"""
        You are an expert Python Pandas data architect. 
        I have a massive DataFrame `df` combining multiple raw reports.
        
        Here are the columns and their datatypes:
        {schema_info}
        
        Here is a 3-row sample of the data to understand the context:
        {sample_data}
        
        USER INSTRUCTIONS:
        {instructions}
        
        Task: Write a complete, syntactically correct Python function named `transform_df(df)` that performs all the requested filtering, renaming, calculations, and column selections.
        - The function must take a single argument `df` (the Pandas DataFrame) and return the modified `df`.
        - Handle any math natively in pandas.
        - CRITICAL DATA TYPE HANDLING: If you need to do math on a column, FORCE it to numeric first. For currency fields (e.g., "$1,234.56"), you MUST clean them: `df['Col'] = pd.to_numeric(df['Col'].astype(str).str.replace(r'[$,]', '', regex=True), errors='coerce')`. Do this for EVERY column involved in a calculation.
        - CRITICAL DATE HANDLING: The data uses Australian dates and may contain timezone strings (e.g., '2/04/2026 2:24 PM AEDT'). You MUST strip the timezone text before converting: `df['Col'] = pd.to_datetime(df['Col'].astype(str).str.replace(r' (AEDT|AEST|AWST|ACST)', '', regex=True), dayfirst=True, errors='coerce')`. 
        - CRITICAL MATH & DURATIONS: Calculate date durations using `(date2 - date1).dt.days`. If you need weekday calculation, use `np.busday_count(date1.values.astype('datetime64[D]'), date2.values.astype('datetime64[D]'))` ensuring to mask out NaT values first. If a required column for any calculation does not exist in the DataFrame (e.g., "Pickup Complete"), DO NOT CRASH. Create the target output column and fill it with `np.nan`.
        - CRITICAL ROW RETENTION: DO NOT use `.dropna()` on the dataset. DO NOT truncate or use `.head()`. Keep all rows. If a date filter is requested, ensure you used `dayfirst=True` so you don't accidentally drop valid Australian dates.
        - You have full access to `import pandas as pd`, `import numpy as np`, `import datetime`, and `import re`.
        - ONLY output the raw Python code block inside ```python ... ```. Do not include markdown explanations.
        """

        response = model.generate_content(prompt)
        response_text = response.text.strip()

        code_match = re.search(r"`{3}python(.*?)`{3}", response_text, re.DOTALL)
        if code_match:
            code_str = code_match.group(1).strip()
        else:
            code_str = response_text.replace("```", "").strip()

        local_vars = {}
        try:
            exec(code_str, {'pd': pd, 'np': np, 'datetime': datetime, 're': re}, local_vars)
            transform_df = local_vars['transform_df']
            final_df = transform_df(main_df)
        except Exception as exec_err:
            return f"Error executing Pandas transformation based on instructions: {sanitize_error_log(str(exec_err))}\n\nAttempted Code:\n{sanitize_error_log(code_str)}"

        drive_scope = base64.b64decode("aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9kcml2ZQ==").decode()
        sheets_scope = base64.b64decode("aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9zcHJlYWRzaGVldHM=").decode()
        
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=[drive_scope, sheets_scope]
        )

        sheets_service = build("sheets", "v4", credentials=creds)
        drive_service = build("drive", "v3", credentials=creds)

        parent_folder_id = "1U8PYxUZMfJql0AYnhc0izJpI0FqveeFR"

        file_metadata = {
            'name': target_sheet_name,
            'mimeType': 'application/vnd.google-apps.spreadsheet',
            'parents': [parent_folder_id]
        }
        
        sheet_file = drive_service.files().create(
            body=file_metadata, 
            fields='id',
            supportsAllDrives=True
        ).execute()
        spreadsheet_id = sheet_file.get('id')

        headers_list = final_df.columns.tolist()
        raw_values = final_df.values.tolist()
        
        scrubbed_values = [headers_list]
        for row in raw_values:
            clean_row = []
            for item in row:
                if pd.isna(item):
                    clean_row.append("")
                else:
                    item_str = str(item)
                    if item_str.lower() in ["nan", "nat", "<na>", "none"]:
                        clean_row.append("")
                    else:
                        clean_row.append(item_str)
            scrubbed_values.append(clean_row)

        try:
            sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
            sheet_id = sheet_metadata['sheets'][0]['properties']['sheetId']
            
            requests_body = {
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": sheet_id,
                                "gridProperties": {
                                    "rowCount": max(1000, len(scrubbed_values) + 100),
                                    "columnCount": max(26, len(headers_list) + 5)
                                }
                            },
                            "fields": "gridProperties(rowCount,columnCount)"
                        }
                    }
                ]
            }
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body=requests_body
            ).execute()
        except Exception as e:
            print(f"Grid expansion warning: {sanitize_error_log(str(e))}")

        body = {
            "values": scrubbed_values
        }
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range="Sheet1!A1",
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        human_email = st.session_state.get("user_email", "")
        if human_email:
            try:
                permission = {
                    "type": "user",
                    "role": "writer",
                    "emailAddress": human_email
                }
                drive_service.permissions().create(
                    fileId=spreadsheet_id,
                    body=permission,
                    fields="id",
                    supportsAllDrives=True
                ).execute()
            except Exception:
                pass 

        sheet_url = "[https://docs.google.com/spreadsheets/d/](https://docs.google.com/spreadsheets/d/)" + spreadsheet_id
        return f"SUCCESS: Hybrid Engine multi-file analysis complete. The dataset was merged, processed natively, and piped into a new Google Sheet inside your BOOF Exports Shared Drive folder. Title: {target_sheet_name} | URL: {sheet_url}"

    except Exception as e:
        return f"HYBRID GEMINI CRASH: {sanitize_error_log(str(e))}"
    finally:
        try:
            del creds, sheets_service, drive_service
        except NameError:
            pass

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
    url = base64.b64decode("aHR0cHM6Ly9hcGkuaHViYXBpLmNvbS9jcm0vdjMvb2JqZWN0cy90aWNrZXRz").decode()
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
# TOOL 8: CARRIER INVOICE AUDITOR
# ==========================================
def tool_8_carrier_invoice_auditor(raw_invoice_text: str, notification_email: str) -> str:
    import google.generativeai as genai
    import json
    import requests
    import pandas as pd
    import io
    import streamlit as st
    import re
    import base64
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    
    try:
        gemini_key = st.secrets.get("GEMINI_API_KEY")
        if not gemini_key:
            return "Error: GEMINI_API_KEY is missing from the telemetry secrets."
        
        genai.configure(api_key=gemini_key)
        
        try:
            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            target_model = None
            preferred = ['models/gemini-1.5-pro', 'models/gemini-1.5-pro-latest', 'models/gemini-1.5-flash', 'models/gemini-1.5-flash-latest']
            
            for pref in preferred:
                if pref in available_models:
                    target_model = pref
                    break
                    
            if not target_model and available_models:
                target_model = available_models[0]
                
            target_model = target_model.replace('models/', '')
            model = genai.GenerativeModel(target_model)
            
        except Exception as model_err:
            return f"HYBRID GEMINI CRASH (Model Auto-Detect Failed): {sanitize_error_log(str(model_err))}"
            
        df_raw = None
        uploaded_files = st.session_state.get("chat_uploader")
        
        if uploaded_files:
            for uf in uploaded_files:
                uf.seek(0)
                try:
                    if uf.name.lower().endswith('.csv'):
                        df_raw = pd.read_csv(uf, sep=None, engine='python')
                    elif uf.name.lower().endswith(('.xls', '.xlsx')):
                        df_raw = pd.read_excel(uf)
                    if df_raw is not None and not df_raw.empty:
                        break
                except:
                    continue
        
        if df_raw is None or df_raw.empty:
            try:
                df_raw = pd.read_csv(io.StringIO(raw_invoice_text), sep='\t')
                if len(df_raw.columns) < 3:
                    df_raw = pd.read_csv(io.StringIO(raw_invoice_text), sep=',')
                if len(df_raw.columns) < 3:
                    df_raw = pd.read_csv(io.StringIO(raw_invoice_text), sep=None, engine='python')
            except Exception as e:
                return f"Error: Could not parse the text into tabular data. {sanitize_error_log(str(e))}"
            
        csv_headers = list(df_raw.columns)
        
        connote_col = None
        amount_col = None
        invoice_col = None
        
        for col in csv_headers:
            cl = str(col).lower().strip()
            if not connote_col and cl in ['connote', 'consignment no', 'consignment number', 'reference', 'carrier connote', 'consignment']:
                connote_col = col
            if not amount_col and cl in ['total amount', 'charge total', 'billed amount', 'total cost', 'amount']:
                amount_col = col
            if not invoice_col and 'invoice' in cl and ('number' in cl or 'no' in cl):
                invoice_col = col
                
        if not connote_col: connote_col = csv_headers[7] if len(csv_headers)>7 else csv_headers[0]
        if not amount_col: 
            for col in csv_headers:
                cl = str(col).lower().strip()
                if 'total' in cl and ('amount' in cl or 'cost' in cl):
                    amount_col = col
                    break
            if not amount_col: amount_col = csv_headers[-3] if len(csv_headers)>3 else csv_headers[-1]
        if not invoice_col:
            invoice_col = csv_headers[5] if len(csv_headers)>5 else None

        invoice_items = []
        for index, row in df_raw.iterrows():
            c_val = str(row.get(connote_col, "")).strip()
            if pd.isna(c_val) or c_val.lower() == "nan" or not c_val:
                continue
                
            a_val = str(row.get(amount_col, "0"))
            try:
                clean_amount = float(re.sub(r'[^\d.-]', '', a_val))
            except:
                clean_amount = 0.0
                
            i_val = str(row.get(invoice_col, "Unknown")).strip() if invoice_col else "Unknown"

            # ==========================================
            # OWASP CONTEXT MINIMIZER (DSGAI15)
            # ==========================================
            # Exclude PII and irrelevant fields from the LLM prompt payload.
            # The AI only needs weight, dimensions, surcharges, and cost to audit a variance.
            pii_keywords = ['name', 'address', 'email', 'phone', 'contact', 'receiver', 'sender', 'attention', 'company', 'town', 'suburb', 'street']
            safe_row_items = []
            for k, v in row.items():
                if pd.isna(v): continue
                if any(pii_kw in str(k).lower() for pii_kw in pii_keywords): continue
                safe_row_items.append(f"{k}: {v}")
            raw_line_str = " | ".join(safe_row_items)
            
            invoice_items.append({
                "connote": c_val,
                "billed_amount": clean_amount,
                "invoice_number": i_val,
                "raw_invoice_line": raw_line_str
            })

        ms_token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
        ms_headers = { "token": ms_token, "Content-Type": "application/json" }
        reconciliation_data = []
        analysis_batch = []

        b64_urls = [
            "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlDYXJyaWVyQ29uc2lnbm1lbnRJZD9pbmNsdWRlQ2hpbGRDb21wYW5pZXM9dHJ1ZQ==",
            "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UxP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl",
            "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UyP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl"
        ]
        search_urls = [base64.b64decode(u).decode() for u in b64_urls]

        for item in invoice_items:
            connote = item.get("connote", "")
            raw_invoice_line = item.get("raw_invoice_line", "N/A")
            billed_amount = item.get("billed_amount", 0.0)
            invoice_number = item.get("invoice_number", "Unknown")

            expected_amount = 0.0
            expected_sell = 0.0
            carrier_name = "Unknown Carrier"
            diagnostic_log = []
            found = False
            ms_metrics = {}

            for url in search_urls:
                try:
                    resp = requests.post(url, headers=ms_headers, json=[connote], timeout=15)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("object") and len(data["object"]) > 0:
                            consignment = data["object"][0]
                            c_total = consignment.get("consignmentTotal") or {}
                            
                            carrier_name = consignment.get("carrier", {}).get("name", "Unknown Carrier")
                            surcharge_list = c_total.get("consignmentCarrierSurcharges", [])
                            surcharge_names = [s.get("carrierSurcharge", {}).get("name", "Unknown Surcharge") for s in surcharge_list]
                            
                            item_list = consignment.get("items", [])
                            item_summary = []
                            for it in item_list:
                                qty = it.get("quantity", 0)
                                wgt = it.get("weight", 0)
                                item_summary.append(f"{qty}x {wgt}kg")
                            
                            ms_metrics = {
                                "machship_weight": consignment.get("totalWeight", 0),
                                "machship_cubic": consignment.get("totalVolume", 0),
                                "machship_base_cost": c_total.get("totalBaseCostPrice", 0),
                                "machship_surcharges_total": c_total.get("totalConsignmentCarrierSurchargesCostPrice", 0),
                                "machship_surcharge_names": surcharge_names,
                                "machship_items": item_summary
                            }
                            
                            # Extract Cost Price
                            cost = c_total.get("totalCostPrice")
                            if cost is None: cost = c_total.get("totalCostBeforeTax")
                            if cost is None: cost = c_total.get("totalCost")
                            if cost is None: cost = c_total.get("cost")
                            if cost is None: cost = consignment.get("totalCostPrice")
                            if cost is None: cost = consignment.get("totalCost")
                            if cost is None: cost = consignment.get("cost")
                            
                            # Extract Sell Price (Customer markup)
                            sell = c_total.get("totalSellPrice")
                            if sell is None: sell = c_total.get("totalSellBeforeTax")
                            if sell is None: sell = c_total.get("totalSell")
                            if sell is None: sell = consignment.get("totalSellPrice")
                            if sell is None: sell = consignment.get("totalSell")
                            
                            if cost is not None:
                                expected_amount = float(cost)
                            else:
                                diagnostic_log.append("Machship 'cost' nodes missing.")
                                
                            if sell is not None:
                                expected_sell = float(sell)
                                
                            found = True
                            break
                        else:
                            diagnostic_log.append(f"Not found via {url.split('/')[-1].split('?')[0]}")
                    else:
                        diagnostic_log.append(f"HTTP {resp.status_code}")
                except requests.exceptions.Timeout:
                    diagnostic_log.append(f"Timeout")
                except Exception as loop_e:
                    diagnostic_log.append(f"Error: {sanitize_error_log(str(loop_e))}")

            if not found:
                diagnostic_log.append("Failed to locate connote in Machship.")

            variance = billed_amount - expected_amount
            
            # Rule 1: Drop consignments if the carrier charged less than Machship anticipated.
            if expected_amount > 0 and variance < -0.05:
                continue

            diag_string = "Clean" if not diagnostic_log else " | ".join(diagnostic_log)
            surcharge_str = ", ".join(ms_metrics.get("machship_surcharge_names", [])) if ms_metrics else "None"

            # Rule 3: Calculate dynamic markup factor and Sell Price to Customer
            if expected_amount > 0.01:
                markup_factor = expected_sell / expected_amount
            else:
                markup_factor = 1.17 # BOOF Fallback Protocol
                
            sell_price_to_customer = round((variance * markup_factor), 2) if variance > 0 else 0.0

            row_data = {
                "Carrier Connote": connote,
                "Billed Amount": billed_amount,
                "Expected Amount": expected_amount,
                "Variance": variance,
                "Sell Price to Customer": sell_price_to_customer,
                "Expected Surcharges": surcharge_str,
                "AI Variance Analysis": "Pending Analysis",
                "Diagnostics": diag_string
            }
            reconciliation_data.append(row_data)

            if found and variance > 0.10:
                analysis_batch.append({
                    "connote": connote,
                    "variance": variance,
                    "carrier_invoice_line": raw_invoice_line,
                    "machship_metrics": ms_metrics
                })

        ai_reasons = {}
        if len(analysis_batch) > 0:
            batch_prompt = f"You are a forensic freight auditor. I am providing a JSON array of {len(analysis_batch)} consignments that have a cost variance. Compare the carrier_invoice_line text against the machship_metrics. Look explicitly for Discrepancies in Weight or Volume, Missing or Added Surcharges, and Base rate mismatches.\n\nCRITICAL INSTRUCTION 1: Try to actively FIGURE OUT the root cause of the discrepancy rather than just reporting the numbers. For example, carriers like FedEx often categorize their fuel levy under the general 'Surcharge' column. You must compare the carrier's 'Surcharge' field against the Machship metrics (including fuel surcharges) to see if that explains the variance.\n\nCRITICAL INSTRUCTION 2: Format your analysis inside 'variance_reason' with logical line breaks. You MUST insert a line break character ('\\n') after EVERY full stop (.) to ensure the text remains short per line in the spreadsheet cell. Structure it using clear prefixes like 'Weight Discrepancy:', 'Base Rate Mismatch:', and 'Missing/Added Surcharges:'.\n\nCRITICAL INSTRUCTION 3: You MUST return exactly {len(analysis_batch)} JSON objects in your array. Do NOT skip any items. Do NOT summarize. Return ONLY a valid JSON array of objects with strictly two keys: 'connote' and 'variance_reason'.\n\nVariance Data: {json.dumps(analysis_batch)}"
            
            try:
                analysis_resp = model.generate_content(
                    batch_prompt,
                    generation_config=genai.GenerationConfig(response_mime_type="application/json")
                )
                analysis_text = analysis_resp.text.strip()
                
                amatch = re.search(r"\[.*\]", analysis_text, re.DOTALL | re.IGNORECASE)
                if amatch:
                    analysis_text = amatch.group(0).strip()

                analysis_results = json.loads(analysis_text)
                for res in analysis_results:
                    ai_reasons[res.get("connote", "")] = res.get("variance_reason", "AI could not determine reason.")
            except Exception as e:
                print(f"Batch AI Analysis Failed: {sanitize_error_log(str(e))}")

        for row in reconciliation_data:
            c_connote = row["Carrier Connote"]
            
            if row["Variance"] <= 0.10:
                row["AI Variance Analysis"] = "No discrepancy (Exact Match)."
            elif c_connote in ai_reasons:
                row["AI Variance Analysis"] = ai_reasons[c_connote]
            elif row["Diagnostics"] != "Clean" and "Not found" in row["Diagnostics"]:
                row["AI Variance Analysis"] = "Cannot analyze - not found in Machship."
            else:
                row["AI Variance Analysis"] = "AI Analysis Skipped."

        df = pd.DataFrame(reconciliation_data)
        col_order = ["Carrier Connote", "Billed Amount", "Expected Amount", "Variance", "Sell Price to Customer", "Expected Surcharges", "AI Variance Analysis", "Diagnostics"]
        df = df[col_order]

        drive_scope = base64.b64decode("aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9kcml2ZQ==").decode()
        sheets_scope = base64.b64decode("aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9zcHJlYWRzaGVldHM=").decode()
        
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=[drive_scope, sheets_scope]
        )

        sheets_service = build("sheets", "v4", credentials=creds)
        drive_service = build("drive", "v3", credentials=creds)

        parent_folder_id = "1U8PYxUZMfJql0AYnhc0izJpI0FqveeFR"
        timestamp_str = pd.Timestamp.now().strftime("%Y%m%d_%H%M")
        target_sheet_name = f"Invoice Audit Output - {timestamp_str}"

        file_metadata = {
            'name': target_sheet_name,
            'mimeType': 'application/vnd.google-apps.spreadsheet',
            'parents': [parent_folder_id]
        }
        
        sheet_file = drive_service.files().create(
            body=file_metadata, 
            fields='id',
            supportsAllDrives=True
        ).execute()
        spreadsheet_id = sheet_file.get('id')

        headers_list = df.columns.tolist()
        raw_values = df.values.tolist()
        
        scrubbed_values = [headers_list]
        for row in raw_values:
            clean_row = []
            for item in row:
                if pd.isna(item):
                    clean_row.append("")
                else:
                    item_str = str(item)
                    if item_str.lower() in ["nan", "nat", "<na>", "none"]:
                        clean_row.append("")
                    else:
                        clean_row.append(item_str)
            scrubbed_values.append(clean_row)

        try:
            sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
            sheet_id = sheet_metadata['sheets'][0]['properties']['sheetId']
            
            requests_body = {
                "requests": [
                    {
                        "updateSheetProperties": {
                            "properties": {
                                "sheetId": sheet_id,
                                "gridProperties": {
                                    "rowCount": max(1000, len(scrubbed_values) + 100),
                                    "columnCount": max(26, len(headers_list) + 5)
                                }
                            },
                            "fields": "gridProperties(rowCount,columnCount)"
                        }
                    }
                ]
            }
            sheets_service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body=requests_body
            ).execute()
        except Exception as grid_e:
            print(f"Grid expansion warning: {sanitize_error_log(str(grid_e))}")

        body = { "values": scrubbed_values }
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range="Sheet1!A1",
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        if notification_email:
            try:
                permission = {
                    "type": "user",
                    "role": "writer",
                    "emailAddress": notification_email
                }
                drive_service.permissions().create(
                    fileId=spreadsheet_id,
                    body=permission,
                    fields="id",
                    supportsAllDrives=True
                ).execute()
            except Exception:
                pass 

        sheet_url = f"[https://docs.google.com/spreadsheets/d/](https://docs.google.com/spreadsheets/d/){spreadsheet_id}"
        return f"SUCCESS: Invoice Auditor complete. Processed {len(invoice_items)} records natively. View Sheet: {sheet_url}"

    except Exception as base_e:
        return f"TOOL 8 CRITICAL CRASH: {sanitize_error_log(str(base_e))}"
    finally:
        try:
            del creds, sheets_service, drive_service
        except NameError:
            pass

# ==========================================
# TOOL 10: TEMPORAL ANOMALY DETECTOR
# ==========================================
def create_hubspot_alert_ticket(ms_number, carrier_name, action_taken, service_key, draft_text=""):
    url = base64.b64decode("aHR0cHM6Ly9hcGkuaHViYXBpLmNvbS9jcm0vdjMvb2JqZWN0cy90aWNrZXRz").decode()
    headers = {
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json"
    }
    
    content_str = f"Status: Carrier Silence Detected (Suspected Missed Pickup).\nAction Taken: {action_taken}\nTime Flagged: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    if draft_text:
        content_str += f"\n\n=== SUGGESTED DRAFT ===\n{draft_text}"
        
    raw_properties = {
        "hs_pipeline": "0",
        "hs_pipeline_stage": "1",
        "subject": f"Freight Alert: {ms_number} ({carrier_name})",
        "content": content_str,
        "hs_ticket_priority": "HIGH"
    }
    clean_properties = sanitize_hubspot_payload(raw_properties)
    payload = { "properties": clean_properties }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        response.raise_for_status()
        return response.json().get("id")
    except Exception as e:
        return f"Error: {sanitize_error_log(str(e))}"

def tool_10_temporal_anomaly_detector():
    diagnostic_logs = []
    
    now = datetime.datetime.now()
    if now.weekday() == 0:
        prev_bday = now - datetime.timedelta(days=3)
    elif now.weekday() == 6:
        prev_bday = now - datetime.timedelta(days=2)
    else:
        prev_bday = now - datetime.timedelta(days=1)
        
    cutoff_threshold = prev_bday.replace(hour=17, minute=0, second=0, microsecond=0)
    
    if now.weekday() == 4:
        next_bday = now + datetime.timedelta(days=3)
    elif now.weekday() == 5:
        next_bday = now + datetime.timedelta(days=2)
    else:
        next_bday = now + datetime.timedelta(days=1)
        
    rebook_target_date = next_bday.replace(hour=9, minute=0, second=0, microsecond=0)
    rebook_target_str = rebook_target_date.strftime("%Y-%m-%dT09:00:00")
    rebook_display_str = rebook_target_date.strftime("%Y-%m-%d 09:00 AM")

    try:
        ms_token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
        
        base_url = base64.b64decode("aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvZ2V0UmVjZW50bHlDcmVhdGVkT3JVcGRhdGVkQ29uc2lnbm1lbnRz").decode()
        edit_url = base64.b64decode("aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvZWRpdA==").decode()
        hs_key = st.secrets.get("hubspot", {}).get("service_key")
        
        from_date = (datetime.datetime.utcnow() - datetime.timedelta(days=14)).strftime('%Y-%m-%dT%H:%M:%SZ')
        headers = { "token": ms_token, "Content-Type": "application/json" }
        
        active_data = []
        start_index = 0
        while True:
            # Reverted to f-strings for URL construction to prevent requests from URL-encoding ISO dates
            # Also injected parent company ID (53031) to correctly anchor the includeChildCompanies fetch
            fetch_url = f"{base_url}?fromDateUtc={from_date}&companyId=53031&includeChildCompanies=true&retrieveSize=200&startIndex={start_index}"
            resp = requests.get(fetch_url, headers=headers, timeout=15)
            resp.raise_for_status()
            
            page_data = resp.json().get('object', [])
            if not page_data:
                break
                
            active_data.extend(page_data)
            
            if len(page_data) < 200:
                break
            start_index += 200
            
        anomalies = []
        for item in active_data:
            status_name = item.get('status', {}).get('name', '').lower()
            if status_name in ['booked', 'manifested', 'unmanifested']:
                # Bulletproof Parsing: Fallback sequence & string slice
                creation_str = item.get('despatchDateLocal') or item.get('despatchDateTimeLocal') or item.get('creationDate')
                if creation_str:
                    try:
                        clean_str = str(creation_str)[:19]
                        creation_dt = datetime.datetime.strptime(clean_str, "%Y-%m-%dT%H:%M:%S")
                        if creation_dt < cutoff_threshold:
                            anomalies.append(item)
                    except Exception as parse_e:
                        print(f"Date Parse Diagnostic MS {item.get('consignmentNumber')}: {sanitize_error_log(str(parse_e))}")
                        
        if not anomalies:
            return "Sweep Complete. No temporal anomalies detected."
            
        action_summary = []
        for anomaly in anomalies:
            ms_number = anomaly.get('consignmentNumber')
            carrier_name = anomaly.get('carrier', {}).get('name', 'Unknown Carrier')
            
            rebook_payload = {
                "id": anomaly.get('id'),
                "despatchDateTimeLocal": rebook_target_str
            }
            
            action_taken = ""
            api_success = False
            try:
                edit_resp = requests.post(edit_url, headers=headers, json=rebook_payload, timeout=15)
                if edit_resp.status_code == 200:
                    api_success = True
                    action_taken = f"API Rebook Successful for {rebook_display_str}."
            except Exception:
                pass
                
            if not api_success:
                action_taken = f"API Unsupported: Manual Email Dispatch Required via HubSpot for {rebook_display_str}."
                    
            if hs_key:
                draft_msg = f"Notification of Missed Pickup.\nMS Number: {ms_number}\nCarrier: {carrier_name}\n\nPlease prioritize collection on {rebook_display_str}."
                create_hubspot_alert_ticket(ms_number, carrier_name, action_taken, hs_key, draft_text=draft_msg if not api_success else "")
                
            action_summary.append(f"{ms_number} ({carrier_name}): {action_taken}")
            
        return f"Sweep Complete. Processed {len(anomalies)} missed pickups.\n" + "\n".join(action_summary)

    except Exception as e:
        return f"TOOL 10 CRITICAL CRASH: {sanitize_error_log(str(e))}"

# ==========================================
# TOOL 11: TRANSIT DELAY AUTO-QUERY ENGINE
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

- Cope Sensitive Freight: 
  If delivering to Sydney or broader NSW (excluding Albury and Newcastle), email nsw@cope.com.au.
  If delivering to Melbourne or broader VIC, email vic@cope.com.au.
  If delivering to Adelaide or broader SA, email sa@cope.com.au.
  If delivering to Brisbane or broader QLD (excluding Cairns and Townsville), email qldcust@cope.com.au.
  If delivering to Perth or broader WA, email wa@cope.com.au.
  If delivering to Albury, email albury@cope.com.au.
  If delivering to Cairns, email cairns@cope.com.au.
  If delivering to Canberra or ACT, email act@cope.com.au.
  If delivering to Darwin or NT, email nt@cope.com.au.
  If delivering to Hobart or broader TAS (excluding Launceston), email tas@cope.com.au.
  If delivering to Launceston, email launceston@cope.com.au.
  If delivering to Newcastle, email newcastle@cope.com.au.
  If delivering to Townsville, email townsville@cope.com.au.

- GKR: 
  If delivering to NSW, email pickups@gkrtransport.com.au. 
  If delivering to VIC, email melbourne@gkrtransport.com.au. 
  If delivering to QLD, email brisbane@gkrtransport.com.au. 
  If delivering to SA or WA, email wapickups@gkrtransport.com.au.
"""

def tool_11_transit_delay_engine(dry_run: bool = False, target_date_override: str = None):
    """
    Executes the autonomous sweep for delayed consignments. 
    Intended to be triggered by GCP Cloud Scheduler at 05:00 AEST (Mon-Fri).
    Includes LLM logic to deduce specific regional routing emails based on delivery location.
    Accepts a 'dry_run' boolean to prevent external API dispatch during testing.
    Accepts a 'target_date_override' string (YYYY-MM-DD) for historical testing.
    """
    import google.generativeai as genai
    now = datetime.datetime.now()
    
    if target_date_override:
        target_date_str = target_date_override
    else:
        # 1. Determine Temporal Target (Previous Business Day)
        if now.weekday() == 0: 
            target_date = now - datetime.timedelta(days=3)
        elif now.weekday() == 6: 
            target_date = now - datetime.timedelta(days=2)
        else: 
            target_date = now - datetime.timedelta(days=1)
        target_date_str = target_date.strftime('%Y-%m-%d')
    
    try:
        # Load API Tokens and Service Accounts
        ms_token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
        hs_key = st.secrets.get("hubspot", {}).get("service_key")
        gemini_key = st.secrets.get("GEMINI_API_KEY")
        
        if not gemini_key:
            return "TOOL 11 CRITICAL CRASH: GEMINI_API_KEY is missing from telemetry secrets."
            
        genai.configure(api_key=gemini_key)
        
        # 2. Fetch Active Consignments via Valid Endpoint
        base_url = base64.b64decode("aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvZ2V0UmVjZW50bHlDcmVhdGVkT3JVcGRhdGVkQ29uc2lnbm1lbnRz").decode()
        
        from_date = (datetime.datetime.utcnow() - datetime.timedelta(days=14)).strftime('%Y-%m-%dT%H:%M:%SZ')
        headers = { "token": ms_token, "Content-Type": "application/json" }
        
        active_data = []
        start_index = 0
        while True:
            # Reverted to f-strings for URL construction to prevent requests from URL-encoding ISO dates
            # Also injected parent company ID (53031) to correctly anchor the includeChildCompanies fetch
            fetch_url = f"{base_url}?fromDateUtc={from_date}&companyId=53031&includeChildCompanies=true&retrieveSize=200&startIndex={start_index}"
            resp = requests.get(fetch_url, headers=headers, timeout=15)
            resp.raise_for_status()
            
            page_data = resp.json().get('object', [])
            if not page_data:
                break
                
            active_data.extend(page_data)
            
            if len(page_data) < 200:
                break
            start_index += 200
        
        # 3. Filter for Exceptions & Extract Geodata
        success_statuses = ['delivered', 'on board for delivery', 'partially delivered', 'awaiting collection', 'completed']
        delayed_freight = []
        
        for item in active_data:
            eta_raw = item.get('expectedDeliveryDate', '')
            eta = eta_raw.split('T')[0] if eta_raw else ''
            status = item.get('status', {}).get('name', '').lower()
            
            if eta == target_date_str and status not in success_statuses:
                to_node = item.get('despatch', {}).get('toLocation', {})
                if not to_node:
                    to_node = item.get('toLocation', {})
                
                suburb = to_node.get('suburb', 'Unknown')
                state = to_node.get('state', 'Unknown')
                postcode = to_node.get('postcode', 'Unknown')
                
                delayed_freight.append({
                    "ms_number": item.get('consignmentNumber'),
                    "carrier_name": item.get('carrier', {}).get('name', 'Unknown Carrier'),
                    "status_display": item.get('status', {}).get('name', 'Unknown Status'),
                    "destination": f"{suburb}, {state} {postcode}"
                })
                
        if not delayed_freight:
            return f"Sweep Complete. No transit delays detected for target date {target_date_str}."

        # 4. LLM Routing Deduction Engine
        routing_prompt = f"""
        You are a highly logical freight routing API. I am giving you a list of plain-text routing rules and a JSON array of delayed consignments containing their carrier and delivery destination.
        
        ROUTING RULES:
        {CARRIER_ROUTING_RULES}
        
        CONSIGNMENTS TO ROUTE:
        {json.dumps(delayed_freight)}
        
        TASK:
        Evaluate each consignment's 'carrier_name' and 'destination' against the routing rules to deduce the correct email address. 
        If a carrier is not mentioned in the rules, or you cannot deduce an email, set it to "UNMAPPED".
        
        CRITICAL: Return ONLY a valid, raw JSON array of objects with strictly two keys: 'ms_number' and 'routed_email'. Do not include markdown blocks or explanations.
        """
        
        try:
            model = genai.GenerativeModel('gemini-1.5-flash')
            llm_resp = model.generate_content(
                routing_prompt,
                generation_config=genai.GenerationConfig(response_mime_type="application/json")
            )
            
            llm_text = llm_resp.text.strip()
            amatch = re.search(r"\[.*\]", llm_text, re.DOTALL | re.IGNORECASE)
            if amatch:
                llm_text = amatch.group(0).strip()
                
            routed_map = json.loads(llm_text)
            
            if isinstance(routed_map, list):
                email_dict = {r.get('ms_number'): r.get('routed_email') for r in routed_map}
            else:
                email_dict = {}
        except Exception as e:
            return f"TOOL 11 CRITICAL CRASH (LLM Routing Engine Failed): {sanitize_error_log(str(e))}"
            
        action_summary = []
        hs_url = base64.b64decode("aHR0cHM6Ly9hcGkuaHViYXBpLmNvbS9jcm0vdjMvb2JqZWN0cy90aWNrZXRz").decode()
        
        # 5. Action & CRM Sync
        for freight in delayed_freight:
            ms_number = freight['ms_number']
            carrier_name = freight['carrier_name']
            status_display = freight['status_display']
            destination = freight['destination']
            
            carrier_email = email_dict.get(ms_number, "UNMAPPED")
            action_taken = ""
            
            if carrier_email == "UNMAPPED":
                action_taken = f"Skipped: LLM could not deduce routing logic for '{carrier_name}' to '{destination}'."
            else:
                if dry_run:
                    action_taken = f"[DRY RUN SAFE MODE] Would dynamically route email to {carrier_email} and sync to HubSpot."
                else:
                    message_text = (
                        f"Hello,\n\n"
                        f"We are requesting a formal status update on consignment {ms_number}. "
                        f"It was due for delivery on {target_date_str} but is currently showing as '{status_display}'.\n\n"
                        f"Please investigate and provide an updated ETA.\n\n"
                        f"Thank you,\nFreight Companies Australia"
                    )
                    action_taken = f"Drafted query for {carrier_email} injected into HubSpot Ticket."
                        
                    # Sync to HubSpot
                    if hs_key:
                        hs_headers = { "Authorization": f"Bearer {hs_key}", "Content-Type": "application/json" }
                        raw_properties = {
                            "hs_pipeline": "0",  
                            "hs_pipeline_stage": "1",  
                            "subject": f"Action Required: Carrier Query for {ms_number} ({carrier_name})",
                            "content": f"An autonomous query requires dispatch regarding a delayed delivery.\n\nConsignment: {ms_number}\nDestination: {destination}\nOriginal ETA: {target_date_str}\nCurrent Status: {status_display}\n\n=== DRAFT EMAIL TO COPY/PASTE FOR {carrier_email} ===\n{message_text}",
                            "hs_ticket_priority": "MEDIUM"
                        }
                        
                        clean_properties = sanitize_hubspot_payload(raw_properties)
                        try:
                            requests.post(hs_url, headers=hs_headers, json={"properties": clean_properties}, timeout=15)
                        except Exception:
                            pass 
                        
            action_summary.append(f"{ms_number} ({carrier_name}): {action_taken}")
            
        return f"Sweep Complete. Processed {len(delayed_freight)} delayed consignments for {target_date_str}.\n" + "\n".join(action_summary)
        
    except Exception as e:
        return f"TOOL 11 CRITICAL CRASH: {sanitize_error_log(str(e))}"
