import requests
import base64
import re
import json
import io
import pandas as pd
import PyPDF2
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ==========================================
# AUTHENTICATION CACHES (PERFORMANCE UPGRADE)
# ==========================================
@st.cache_data(ttl=1800, show_spinner=False)  # Cache for 30 mins
def get_xero_token():
    try:
        # Added ["xero"] to map to your TOML headers
        client_id = st.secrets["xero"]["XERO_CLIENT_ID"]
        client_secret = st.secrets["xero"]["XERO_CLIENT_SECRET"]
        credentials = f"{client_id}:{client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        
        url = "https://identity.xero.com/connect/token"
        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = { "grant_type": "client_credentials" }
        
        response = requests.post(url, headers=headers, data=data, timeout=15)
        response.raise_for_status()
        return response.json()["access_token"]
    except Exception as e:
        return f"Error: {str(e)}"


@st.cache_data(ttl=3000, show_spinner=False)  # Cache for 50 mins
def get_cartoncloud_token():
    try:
        client_id = st.secrets["cartoncloud"]["client_id"].strip()
        client_secret = st.secrets["cartoncloud"]["client_secret"].strip()
        base_url = "https://api.cartoncloud.com"

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
        return f"Error: {str(e)}"

# ==========================================
# TOOL 1: XERO FINANCIAL SEARCH
# ==========================================
def search_xero_contact(contact_name: str) -> str:
    token = get_xero_token()
    if "Error" in token: return f"Xero Auth {token}" 
    
    # URL encoded string safety
    safe_name = requests.utils.quote(contact_name)
    url = f'https://api.xero.com/api.xro/2.0/Contacts?where=Name.Contains("{safe_name}")'
    headers = { "Authorization": f"Bearer {token}", "Accept": "application/json" }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        if data.get("Contacts"):
            contact = data["Contacts"][0]
            name = contact.get("Name", "Unknown")
            status = contact.get("ContactStatus", "Unknown")
            
            balances = contact.get("Balances", {}).get("AccountsReceivable", {})
            outstanding = balances.get("Outstanding", 0.00)
            overdue = balances.get("Overdue", 0.00)
            
            raw_data = json.dumps(contact, indent=2)
            return f"✅ Xero Record: {name} | Status: {status} | Total Outstanding: ${outstanding} | Overdue: ${overdue}\n\n**Raw Data Available to AI:**\n```json\n{raw_data}\n```"
        else:
            return f"No contact found in Xero matching '{contact_name}'."
    except Exception as e:
        return f"🚨 Xero API Error: {str(e)}"

# ==========================================
# TOOL 2: UNRESTRICTED MACHSHIP SEARCH
# ==========================================
def search_machship_connote(connote_number: str) -> str:
    # Added ["machship"] to map to your TOML headers
    token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
    connote_number = connote_number.strip().upper()
    headers = { "token": token, "Accept": "application/json" }

    try:
        # PATH A: It is an internal Machship number
        if connote_number.startswith("MS"):
            ms_id = re.sub(r"\D", "", connote_number)
            url = f"https://live.machship.com/apiv2/consignments/getConsignment?id={ms_id}"
            response = requests.get(url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("object"):
                    consignment = data["object"]
                    carrier = consignment.get("carrier", {}).get("name") or consignment.get("carrier", {}).get("abbreviation") or "Carrier Not Assigned"
                    status = consignment.get("status", {}).get("name", "Unknown Status")
                    
                    raw_data = json.dumps(consignment, indent=2)
                    return f"✅ Machship Record (MS): Carrier: {carrier} | Status: {status}\n\n**Raw Data Available to AI:**\n```json\n{raw_data}\n```"

        # PATH B: Carrier ID & Reference Hunt
        headers["Content-Type"] = "application/json"
        search_routes = [
            ("Carrier ID", "https://live.machship.com/apiv2/consignments/returnConsignmentsByCarrierConsignmentId?includeChildCompanies=true"),
            ("Reference 1", "https://live.machship.com/apiv2/consignments/returnConsignmentsByReference1?includeChildCompanies=true"),
            ("Reference 2", "https://live.machship.com/apiv2/consignments/returnConsignmentsByReference2?includeChildCompanies=true")
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
        return f"🚨 Machship API Error: {str(e)}"

# ==========================================
# TOOL 3: TRANSVIRTUAL CONSIGNMENT SEARCH
# ==========================================
def search_transvirtual_connote(connote_number: str) -> str:
    import json
    import requests
    import streamlit as st

    try:
        # 1. Map to the nested TOML structure
        token = st.secrets["transvirtual"]["TRANSVIRTUAL_API_KEY"]
        connote_number = connote_number.strip().upper()

        headers = {
            "Authorization": token, 
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        # STEP 1: Fetch Consignment Details (The Booking Data)
        url_query = "https://api.transvirtual.com.au/api/ConsignmentQuery"
        response_query = requests.post(url_query, headers=headers, json={"ConsignmentNumber": connote_number}, timeout=15)
        full_data = response_query.json().get("Data", {}) if response_query.status_code == 200 else {}

        # STEP 2: The Tracking Extraction
        url_status = "https://api.transvirtual.com.au/api/ConsignmentStatus"
        tracking_data = None
        tracking_log = []

        # 1st Attempt: Standard shape
        payload_status = {"Number": connote_number}
        response_status = requests.post(url_status, headers=headers, json=payload_status, timeout=15)
        
        if response_status.status_code == 200 and "Missing" not in response_status.text:
            tracking_data = response_status.json().get("Data", response_status.json())
        else:
            tracking_log.append(f"Standard Payload Failed: HTTP {response_status.status_code}")
            
            # Fallback: The 4 most common enterprise payload shapes
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

        # Combine the Data for Digital Marsh
        combined_matrix = {
            "ConsignmentDetails": full_data,
            "TrackingScans": tracking_data if tracking_data else "Failed tracking X-Ray: " + " | ".join(tracking_log)
        }

        raw_matrix = json.dumps(combined_matrix, indent=2)

        return f"✅ Transvirtual Record: {connote_number}\n\n**Raw Data Available to AI:**\n```json\n{raw_matrix}\n```"

    except requests.exceptions.Timeout:
        return "🚨 Transvirtual API Error: The server timed out."
    except Exception as e:
        return f"🚨 Transvirtual API Crash: {str(e)}"

# ==========================================
# TOOL 4: GOOGLE DRIVE ORACLE
# ==========================================
def search_and_read_google_drive(search_query: str) -> str:
    try:
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=['https://www.googleapis.com/auth/drive.readonly']
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
        return f"🚨 Google Drive Connection Crash: {str(e)}"

# ==========================================
# TOOL 5: CARTON CLOUD WMS ORACLE
# ==========================================
def search_cartoncloud_order(reference_number: str) -> str:
    try:
        tenant_id = st.secrets["cartoncloud"]["tenant_id"].strip()
        base_url = "https://api.cartoncloud.com"
        
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
        return f"🚨 Carton Cloud API Error: {str(e)}"

# ==========================================
# TOOL 6: MASS MATRIX PROCESSOR (FLIGHT COMPUTER)
# ==========================================
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_australian_postcodes():
    import requests
    import csv
    url = "https://raw.githubusercontent.com/matthewproctor/australianpostcodes/master/australian_postcodes.csv"
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
        # 1. Read the CSV Payload
        df = pd.read_csv(io.BytesIO(file_bytes))
        pc_db = fetch_australian_postcodes()
        
        # Helper function to dynamically map messy client columns
        def get_val(row_s, possible_cols, default=""):
            for col in possible_cols:
                if col in row_s and pd.notna(row_s[col]):
                    return str(row_s[col]).strip()
            return default
            
        # 2. Setup Dispatch Date (Next Business Day)
        next_day = datetime.now() + timedelta(days=1)
        while next_day.weekday() >= 5:  
            next_day += timedelta(days=1)
        dispatch_date = next_day.strftime("%Y-%m-%dT09:00:00")

        token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
        url = "https://live.machship.com/apiv2/routes/returnroutes"
        headers = {"token": token, "Content-Type": "application/json"}
        company_id = 53031 

        # 3. The Ping Function (Runs for a single row)
        def fetch_route(index, row):
            # Dynamic Column Mapping
            to_sub = get_val(row, ["Destination", "To Suburb", "To", "Suburb"], "")
            to_post = get_val(row, ["To PC", "Postcode"], "").replace(".0", "")
            
            from_sub = get_val(row, ["From", "From Suburb", "Origin"], "Seaford")
            from_post = get_val(row, ["From PC", "Origin Postcode"], "3198").replace(".0", "")
            
            # Suburb Reverse Lookup (Fixes abbreviations like 'SYDN' or 'MELB')
            if len(from_sub) <= 4 and from_post in pc_db:
                from_sub = pc_db[from_post]
            if len(to_sub) <= 4 and to_post in pc_db:
                to_sub = pc_db[to_post]

            # Freight Data Extraction
            qty_items = float(get_val(row, ["Items"], 0))
            qty_pallets = float(get_val(row, ["Pallets"], 0))
            weight = float(get_val(row, ["KGS", "Weight", "Total Weight", "Charged KGs"], 0))
            cubic = float(get_val(row, ["Cubic", "Volume"], 0))

            # Determine Item Type
            if qty_pallets > 0:
                qty = int(qty_pallets)
                item_name = "Pallet"
            elif qty_items > 0:
                qty = int(qty_items)
                item_name = "Carton"
            else:
                qty = 1
                item_name = "Item"

            # Failsafe against empty rows causing divide-by-zero crashes
            if qty <= 0: qty = 1
            weight_per_item = weight / qty if weight > 0 else 1.0
            cubic_per_item = cubic / qty if cubic > 0 else 0.001
            
            # Machship requires dimensions. We perfectly math the cubic volume back into CM.
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
                    
                    # FILTER: Check against user's excluded carriers
                    if any(ex.lower() in raw_carrier_name.lower() for ex in excluded_carriers):
                        continue
                        
                    # EXTRACT: Account Name & Service (Corrected JSON Path)
                    acc_node = r.get('companyCarrierAccount') or r.get('carrierAccount') or {}
                    acc_name = acc_node.get('name') or acc_node.get('accountCode') or ''
                    
                    service_name = r.get('companyCarrierAccountService', {}).get('name') or r.get('carrierService', {}).get('name') or ''
                    
                    # Construct transparent display name
                    display_name = raw_carrier_name
                    if service_name: 
                        display_name += f" - {service_name}"
                    if acc_name: 
                        display_name += f" [{acc_name}]"

                    c_total = r.get('consignmentTotal') or {}
                    
                    # Apply dynamic GP margin to base cost
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
                    # Sort by price
                    valid_routes.sort(key=lambda x: x['price'])
                    
                    # Ensure we grab 3 unique carriers (not just 3 services from the same carrier)
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
                return index, f"Crash: {str(e)}", []

        # 4. The Multi-Threaded Swarm
        with ThreadPoolExecutor(max_workers=15) as executor:
            future_to_row = {executor.submit(fetch_route, index, row): index for index, row in df.iterrows()}
            
            for future in as_completed(future_to_row):
                idx, status, options = future.result()
                
                # We append the new data directly next to the original client columns!
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
        return False, f"Matrix Engine Crash: {str(e)}"

# ==========================================
# TOOL 7: HYBRID GEMINI SHEET GENERATOR (HARDCODED FOLDER ID)
# ==========================================
def hybrid_gemini_sheet_generator(instructions: str, target_sheet_name: str) -> str:
    import google.generativeai as genai
    import pandas as pd
    import io
    import csv
    import streamlit as st
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    try:
        # 1. Fetch the Heavy Data safely via Streamlit session state (The Bypass)
        uploaded_files = st.session_state.get("chat_uploader")
        if not uploaded_files:
            return "Error: No files currently uploaded in the Oracle Data Ingestion port. Please upload payloads first."

        # Extract full text/data from ALL uploaded files without Pandas crashing on CSVs
        full_data_string = ""
        for uf in uploaded_files:
            file_extension = uf.name.split(".")[-1].lower()
            
            if file_extension == "csv":
                raw_text = uf.getvalue().decode('utf-8', errors='replace')
                full_data_string += f"\n=== FILE: {uf.name} ===\n{raw_text}\n"
            elif file_extension in ["xlsx", "xls"]:
                uf.seek(0)
                df = pd.read_excel(uf)
                full_data_string += f"\n=== FILE: {uf.name} ===\n{df.to_csv(index=False)}\n"
            else:
                return f"Error: The uploaded file {uf.name} must be a CSV or Excel spreadsheet for the Gemini Matrix generator."

        # 2. Configure Gemini API (Model Auto-Detect)
        gemini_key = st.secrets.get("GEMINI_API_KEY")
        if not gemini_key:
            return "Error: GEMINI_API_KEY is missing from the telemetry secrets."

        genai.configure(api_key=gemini_key)
        
        try:
            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            
            if 'models/gemini-1.5-pro-latest' in available_models:
                target_model = 'gemini-1.5-pro-latest'
            elif 'models/gemini-1.5-pro' in available_models:
                target_model = 'gemini-1.5-pro'
            elif 'models/gemini-1.5-flash-latest' in available_models:
                target_model = 'gemini-1.5-flash-latest'
            elif 'models/gemini-1.5-flash' in available_models:
                target_model = 'gemini-1.5-flash'
            elif 'models/gemini-pro' in available_models:
                target_model = 'gemini-pro'
            elif available_models:
                target_model = available_models[0].replace('models/', '') 
            else:
                return "Error: No valid text generation models found for this API key."
                
            target_model = target_model.replace('models/', '')
            model = genai.GenerativeModel(target_model)
            
        except Exception as model_err:
            return f"HYBRID GEMINI CRASH (Model Auto-Detect Failed): {str(model_err)}"

        # 3. Formulate the prompt for Gemini
        system_instruction = "You are an enterprise data extraction AI. You will receive instructions and raw data from one or multiple files. You MUST cross-reference the data as instructed and output your final answer as pure CSV text. Do not include markdown formatting. Do not include conversational text."
        full_prompt = system_instruction + "\n\nUSER INSTRUCTIONS:\n" + instructions + "\n\nRAW DATA BASKET:\n" + full_data_string

        # 4. Execute Gemini
        response = model.generate_content(full_prompt)
        gemini_csv_output = response.text.strip()

        # Safely strip any formatting without breaking text rules
        forbidden_char = chr(96)
        gemini_csv_output = gemini_csv_output.replace(forbidden_char + "csv", "")
        gemini_csv_output = gemini_csv_output.replace(forbidden_char, "")
        gemini_csv_output = gemini_csv_output.strip()

        # 5. Connect to Google Sheets & Drive via GCP
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]
        )

        sheets_service = build("sheets", "v4", credentials=creds)
        drive_service = build("drive", "v3", credentials=creds)

        # Parse Gemini CSV output into a list of lists for Google Sheets
        csv_reader = csv.reader(io.StringIO(gemini_csv_output))
        values = list(csv_reader)

        if not values:
            return "Error: Gemini processed the data but returned an empty structural matrix."

        # 6. Use the EXACT Folder ID provided by Mission Control
        parent_folder_id = "1U8PYxUZMfJql0AYnhc0izJpI0FqveeFR"

        # 7. Create the Spreadsheet INSIDE the target Shared Drive folder using Drive API
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

        # 8. Write Data to the Sheet
        body = {
            "values": values
        }
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range="Sheet1",
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        # 9. Transfer Ownership / Share (Silent fail if you already have access via Shared Drive)
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

        sheet_url = "https://docs.google.com/spreadsheets/d/" + spreadsheet_id
        return "SUCCESS: Hybrid Gemini multi-file analysis complete. The dataset was piped into a new Google Sheet inside your 'BOOF Exports' Shared Drive folder. Title: " + target_sheet_name + " | URL: " + sheet_url

    except Exception as e:
        return "HYBRID GEMINI CRASH: " + str(e)


# ==========================================
# TOOL 8: CARRIER INVOICE AUDITOR
# ==========================================
def tool_8_carrier_invoice_auditor(raw_invoice_text: str, notification_email: str) -> str:
    """
    Orchestrates the entire Phase 4 Invoice Reconciliation process.
    """
    import google.generativeai as genai
    import json
    import requests
    import pandas as pd
    import streamlit as st
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    
    try:
        # --- 1. GEMINI EXTRACTION WITH MODEL AUTO-DETECT ---
        gemini_key = st.secrets.get("GEMINI_API_KEY")
        if not gemini_key:
            return "Error: GEMINI_API_KEY is missing from the telemetry secrets."
        
        genai.configure(api_key=gemini_key)
        
        try:
            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
            
            if 'models/gemini-1.5-pro-latest' in available_models:
                target_model = 'gemini-1.5-pro-latest'
            elif 'models/gemini-1.5-pro' in available_models:
                target_model = 'gemini-1.5-pro'
            elif 'models/gemini-1.5-flash-latest' in available_models:
                target_model = 'gemini-1.5-flash-latest'
            elif 'models/gemini-1.5-flash' in available_models:
                target_model = 'gemini-1.5-flash'
            elif 'models/gemini-pro' in available_models:
                target_model = 'gemini-pro'
            elif available_models:
                target_model = available_models[0].replace('models/', '') 
            else:
                return "Error: No valid text generation models found for this API key."
                
            target_model = target_model.replace('models/', '')
            model = genai.GenerativeModel(target_model)
            
        except Exception as model_err:
            return f"HYBRID GEMINI CRASH (Model Auto-Detect Failed): {str(model_err)}"
        
        prompt = f"""
        You are an expert freight data extraction assistant. 
        Analyze the following raw carrier invoice text and extract every single shipment.
        
        Return ONLY a valid JSON array of objects. Do not include markdown formatting or extra text.
        Each object must have the following keys:
        - "connote": The carrier consignment note number / reference string (e.g. MS60179596, CIR000000048)
        - "billed_amount": The total cost billed by the carrier for this connote (float)
        
        Raw Invoice Text:
        {raw_invoice_text}
        """
        
        try:
            response = model.generate_content(
                prompt,
                generation_config=genai.GenerationConfig(response_mime_type="application/json")
            )
            extracted_items = json.loads(response.text)
        except Exception as e:
            return f"Error during Gemini invoice extraction: {str(e)}"
            
        if not extracted_items:
            return "Error: Failed to identify any connotes or billed amounts in the provided text."

        # --- 2. MACHSHIP QUOTE LOOKUP & VARIANCE ---
        token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
        headers = {"token": token, "Content-Type": "application/json"}
        ms_url = "https://live.machship.com/apiv2/consignments/returnConsignmentsByCarrierConsignmentId?includeChildCompanies=true"
        
        reconciliation_data = []
        
        for item in extracted_items:
            connote = item.get("connote", "")
            billed = float(item.get("billed_amount", 0.0))
            quoted = 0.0
            
            if connote:
                try:
                    payload = [connote]
                    ms_response = requests.post(ms_url, headers=headers, json=payload, timeout=15)
                    if ms_response.status_code == 200:
                        data = ms_response.json()
                        if data.get("object") and len(data["object"]) > 0:
                            # Safely extract base cost
                            c_total = data["object"][0].get('consignmentTotal', {})
                            quoted = float(c_total.get('totalCost', 0.0))
                except Exception:
                    pass # Keep quoted as 0.0 on failure so variance flags it heavily
                    
            variance = billed - quoted
            flag_status = "FLAG: OVERCHARGE" if variance > 0.01 else "OK"
            if quoted == 0.0:
                flag_status = "FLAG: NO QUOTE FOUND"
                
            reconciliation_data.append({
                "Connote": connote,
                "Billed Amount ($)": round(billed, 2),
                "Quoted Amount ($)": round(quoted, 2),
                "Variance ($)": round(variance, 2),
                "Status": flag_status
            })

        # --- 3. GOOGLE SHEETS GENERATION (Using Tool 7's Shared Drive Logic) ---
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]
        )

        sheets_service = build("sheets", "v4", credentials=creds)
        drive_service = build("drive", "v3", credentials=creds)

        # Hardcoded Shared Drive Folder ID from Tool 7
        parent_folder_id = "1U8PYxUZMfJql0AYnhc0izJpI0FqveeFR"
        sheet_title = f"Invoice_Reconciliation_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}"
        
        file_metadata = {
            'name': sheet_title,
            'mimeType': 'application/vnd.google-apps.spreadsheet',
            'parents': [parent_folder_id]
        }
        
        sheet_file = drive_service.files().create(
            body=file_metadata, 
            fields='id',
            supportsAllDrives=True
        ).execute()
        spreadsheet_id = sheet_file.get('id')

        # Convert Dicts to List of Lists
        headers_list = list(reconciliation_data[0].keys())
        values = [headers_list]
        for row in reconciliation_data:
            values.append([str(row.get(h, "")) for h in headers_list])

        body = {"values": values}
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range="Sheet1",
            valueInputOption="USER_ENTERED",
            body=body
        ).execute()

        # Share it with the user who requested it
        try:
            permission = {'type': 'user', 'role': 'writer', 'emailAddress': notification_email}
            drive_service.permissions().create(
                fileId=spreadsheet_id, 
                body=permission, 
                sendNotificationEmail=True,
                supportsAllDrives=True
            ).execute()
        except Exception as share_err:
            print(f"Warning: Could not auto-share via email {notification_email}. Error: {share_err}")

        sheet_url = "https://docs.google.com/spreadsheets/d/" + spreadsheet_id
        return f"SUCCESS: Invoice Reconciliation complete. {len(reconciliation_data)} shipments audited. Report generated in Shared Drive: {sheet_url}"

    except Exception as e:
        return f"🚨 TOOL 8 CRASH: {str(e)}"
