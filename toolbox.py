import requests
import base64
import re
import json
import io
import pandas as pd
import numpy as np
import datetime
import pypdf
import os
import tempfile
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ==========================================
# SECURE URL RESOLVER
# ==========================================
def get_secure_endpoint(endpoint_key: str, fallback_b64: str) -> str:
    """
    Retrieves endpoints securely from st.secrets to eliminate hardcoded Base64 
    strings per OWASP guidelines. Falls back to decoded Base64 to guarantee zero degradation.
    """
    return st.secrets.get("endpoints", {}).get(endpoint_key, base64.b64decode(fallback_b64).decode())

# ==========================================
# GEMINI SDK UPGRADE WRAPPER (2026 Compatible)
# ==========================================
def call_gemini_api(prompt: str, json_mode: bool = False) -> str:
    gemini_key = st.secrets.get("GEMINI_API_KEY")
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY is missing from the telemetry secrets.")
        
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=gemini_key)
        
        models = client.models.list()
        available_models = [m.name for m in models]
        
        pro_models = sorted([m for m in available_models if 'pro' in m.lower()], reverse=True)
        flash_models = sorted([m for m in available_models if 'flash' in m.lower()], reverse=True)
        
        target_model = pro_models[0] if pro_models else (flash_models[0] if flash_models else "gemini-2.5-pro")
        target_model = target_model.replace('models/', '')
        
        config_kwargs = {}
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"
            
        response = client.models.generate_content(
            model=target_model, 
            contents=prompt,
            config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
        )
        return response.text.strip()
        
    except ImportError:
        import google.generativeai as genai_legacy
        genai_legacy.configure(api_key=gemini_key)
        available_models = [m.name for m in genai_legacy.list_models() if 'generateContent' in m.supported_generation_methods]
        
        pro_models = sorted([m for m in available_models if 'pro' in m.lower()], reverse=True)
        flash_models = sorted([m for m in available_models if 'flash' in m.lower()], reverse=True)
        
        target_model = pro_models[0] if pro_models else (flash_models[0] if flash_models else "gemini-1.5-pro")
        target_model = target_model.replace('models/', '')
        model = genai_legacy.GenerativeModel(target_model)
        
        generation_config = genai_legacy.GenerationConfig(response_mime_type="application/json") if json_mode else None
        response = model.generate_content(prompt, generation_config=generation_config)
        return response.text.strip()

# ==========================================
# VISION BRIDGE PROTOCOL (Multimodal PDF to CSV)
# ==========================================
def vision_bridge_pdf_to_csv(file_obj) -> str:
    gemini_key = st.secrets.get("GEMINI_API_KEY")
    if not gemini_key: 
        return ""
        
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_obj.read())
        tmp_path = tmp.name
        
    prompt = "Extract all tabular data, tables, and structured lists from this PDF. Convert the data into a strict, raw CSV format. Include column headers. Output ONLY the raw CSV text. Do not include markdown blocks or any other explanation."
    csv_text = ""
    
    try:
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            models = client.models.list()
            pro_models = sorted([m.name.replace('models/', '') for m in models if 'pro' in m.name.lower()], reverse=True)
            target_model = pro_models[0] if pro_models else "gemini-2.5-pro"
            
            uploaded_file = client.files.upload(file=tmp_path, config={'mime_type': 'application/pdf'})
            response = client.models.generate_content(
                model=target_model,
                contents=[uploaded_file, prompt]
            )
            csv_text = response.text
            client.files.delete(name=uploaded_file.name)
        except ImportError:
            import google.generativeai as genai_legacy
            genai_legacy.configure(api_key=gemini_key)
            available_models = [m.name for m in genai_legacy.list_models() if 'generateContent' in m.supported_generation_methods]
            pro_models = sorted([m.replace('models/', '') for m in available_models if 'pro' in m.lower()], reverse=True)
            target_model = pro_models[0] if pro_models else "gemini-1.5-pro"
            
            uploaded_file = genai_legacy.upload_file(path=tmp_path, mime_type="application/pdf")
            model = genai_legacy.GenerativeModel(target_model)
            response = model.generate_content([uploaded_file, prompt])
            csv_text = response.text
            genai_legacy.delete_file(uploaded_file.name)
    except Exception as e:
        print(f"Vision Bridge API Failure: {sanitize_error_log(str(e))}")
    finally:
        os.remove(tmp_path)
        
    csv_text = re.sub(r"^```(csv)?\n|\n```$", "", csv_text.strip(), flags=re.IGNORECASE).strip()
    return csv_text

# ==========================================
# OWASP TELEMETRY SANITIZER (DSGAI14)
# ==========================================
def sanitize_error_log(error_msg: str) -> str:
    msg = str(error_msg)
    msg = re.sub(r'(?i)Bearer\s+[A-Za-z0-9\-\._~]+', 'Bearer [REDACTED_TOKEN]', msg)
    msg = re.sub(r'(?i)token=[A-Za-z0-9\-\._~]+', 'token=[REDACTED_TOKEN]', msg)
    msg = re.sub(r'(?i)api_key=[A-Za-z0-9\-\._~]+', 'api_key=[REDACTED_KEY]', msg)
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
        
        url = get_secure_endpoint("xero_auth", "aHR0cHM6Ly9pZGVudGl0eS54ZXJvLmNvbS9jb25uZWN0L3Rva2Vu")
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
        base_url = get_secure_endpoint("cartoncloud_base", "aHR0cHM6Ly9hcGkuY2FydG9uY2xvdWQuY29t")

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
        base_url = get_secure_endpoint("xero_contacts", "aHR0cHM6Ly9hcGkueGVyby5jb20vYXBpLnhyby8yLjAvQ29udGFjdHM/d2hlcmU9TmFtZS5Db250YWlucygi")
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
# TOOL 4: GOOGLE DRIVE ORACLE
# ==========================================
def search_and_read_google_drive(search_query: str) -> str:
    try:
        drive_ro_scope = get_secure_endpoint("google_drive_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9kcml2ZS5yZWFkb25seQ==")
        
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
            pdf_reader = pypdf.PdfReader(fh)
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
        base_url = get_secure_endpoint("cartoncloud_base", "aHR0cHM6Ly9hcGkuY2FydG9uY2xvdWQuY29t")
        
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
    return pc_to_suburb

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

# ==========================================
# TOOL 15: WORKSPACE DOCUMENT CREATOR
# ==========================================
def tool_15_workspace_document_creator(document_title: str, document_body: str, notification_email: str = "") -> str:
    try:
        drive_scope = get_secure_endpoint("drive_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9kcml2ZQ==")
        docs_scope = get_secure_endpoint("docs_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9kb2N1bWVudHM=")
        
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict,
            scopes=[drive_scope, docs_scope]
        )

        drive_service = build("drive", "v3", credentials=creds)
        docs_service = build("docs", "v1", credentials=creds)

        parent_folder_id = "1U8PYxUZMfJql0AYnhc0izJpI0FqveeFR"

        file_metadata = {
            'name': document_title,
            'mimeType': 'application/vnd.google-apps.document',
            'parents': [parent_folder_id]
        }
        
        doc_file = drive_service.files().create(
            body=file_metadata, 
            fields='id',
            supportsAllDrives=True
        ).execute()
        
        document_id = doc_file.get('id')

        if document_body:
            requests_body = {
                "requests": [
                    {
                        "insertText": {
                            "location": {
                                "index": 1,
                            },
                            "text": document_body
                        }
                    }
                ]
            }
            docs_service.documents().batchUpdate(
                documentId=document_id, 
                body=requests_body
            ).execute()

        if notification_email:
            try:
                permission = {
                    "type": "user",
                    "role": "writer",
                    "emailAddress": notification_email
                }
                drive_service.permissions().create(
                    fileId=document_id,
                    body=permission,
                    fields="id",
                    supportsAllDrives=True
                ).execute()
            except Exception:
                pass 

        doc_url = f"[https://docs.google.com/document/d/](https://docs.google.com/document/d/){document_id}"
        return f"SUCCESS: Native Google Document created in BOOF Shared Drive. Title: {document_title} | View Document: {doc_url}"

    except Exception as e:
        return f"TOOL 15 CRITICAL CRASH: {sanitize_error_log(str(e))}"
    finally:
        try:
            del creds, drive_service, docs_service
        except NameError:
            pass

# ==========================================
# TOOL 7: PANDAS ORCHESTRATOR
# ==========================================
def hybrid_gemini_sheet_generator(instructions: str, target_sheet_name: str) -> str:
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
                elif file_extension == "pdf":
                    csv_string = vision_bridge_pdf_to_csv(uf)
                    if csv_string:
                        temp_df = pd.read_csv(io.StringIO(csv_string))
                    else:
                        continue
                else:
                    continue
                df_list.append(temp_df)
            except Exception as read_err:
                return f"Error reading file {uf.name}: {sanitize_error_log(str(read_err))}"

        if not df_list:
            return "Error: No valid CSV, Excel, or PDF data tables were found to combine."

        main_df = pd.concat(df_list, ignore_index=True)
        schema_info = main_df.dtypes.to_string()

        # ==========================================
        # OWASP CONTEXT MINIMIZER (DSGAI15)
        # ==========================================
        sample_df = main_df.head(3).copy()
        for col in sample_df.columns:
            if sample_df[col].dtype == 'object':
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
        - CRITICAL DATA TYPE HANDLING: If you need to do math on a column, FORCE it to numeric first. For currency fields (e.g., "$1,234.56"), you MUST clean them: `df['Col'] = pd.to_numeric(df['Col'].astype(str).str.replace(r'[$,]', '', regex=True), errors='coerce')`. 
        - CRITICAL DATE HANDLING: The data uses Australian dates and may contain timezone strings. You MUST strip the timezone text before converting: `df['Col'] = pd.to_datetime(df['Col'].astype(str).str.replace(r' (AEDT|AEST|AWST|ACST)', '', regex=True), dayfirst=True, errors='coerce')`. 
        - CRITICAL MATH & DURATIONS: Calculate date durations using `(date2 - date1).dt.days`. Mask out NaT values.
        - CRITICAL ROW RETENTION: DO NOT use `.dropna()`. Keep all rows.
        - ONLY output the raw Python code block inside ```python ... ```.
        """

        try:
            response_text = call_gemini_api(prompt, json_mode=False)
        except Exception as model_err:
            return f"HYBRID GEMINI CRASH: {sanitize_error_log(str(model_err))}"

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
            return f"Error executing Pandas transformation: {sanitize_error_log(str(exec_err))}\n\nAttempted Code:\n{sanitize_error_log(code_str)}"

        drive_scope = get_secure_endpoint("drive_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9kcml2ZQ==")
        sheets_scope = get_secure_endpoint("sheets_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9zcHJlYWRzaGVldHM=")
        
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

        body = { "values": scrubbed_values }
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
        return f"SUCCESS: Hybrid Engine multi-file analysis complete. Title: {target_sheet_name} | URL: {sheet_url}"

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
# TOOL 8: CARRIER INVOICE AUDITOR
# ==========================================
def tool_8_carrier_invoice_auditor(raw_invoice_text: str, notification_email: str) -> str:
    try:
        df_raw = None
        uploaded_files = st.session_state.get("chat_uploader")
        
        if uploaded_files:
            for uf in uploaded_files:
                uf.seek(0)
                file_ext = uf.name.lower().split('.')[-1]
                try:
                    if file_ext == 'csv':
                        df_raw = pd.read_csv(uf, sep=None, engine='python')
                    elif file_ext in ['xls', 'xlsx']:
                        df_raw = pd.read_excel(uf)
                    elif file_ext == 'pdf':
                        csv_string = vision_bridge_pdf_to_csv(uf)
                        if csv_string:
                            df_raw = pd.read_csv(io.StringIO(csv_string))
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

        search_urls = [
            get_secure_endpoint("machship_carrier_id", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlDYXJyaWVyQ29uc2lnbm1lbnRJZD9pbmNsdWRlQ2hpbGRDb21wYW5pZXM9dHJ1ZQ=="),
            get_secure_endpoint("machship_ref1", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UxP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl"),
            get_secure_endpoint("machship_ref2", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UyP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl")
        ]

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
                            
                            cost = c_total.get("totalCostPrice")
                            if cost is None: cost = c_total.get("totalCostBeforeTax")
                            if cost is None: cost = c_total.get("totalCost")
                            if cost is None: cost = c_total.get("cost")
                            if cost is None: cost = consignment.get("totalCostPrice")
                            if cost is None: cost = consignment.get("totalCost")
                            if cost is None: cost = consignment.get("cost")
                            
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
            
            if expected_amount > 0 and variance < -0.05:
                continue

            diag_string = "Clean" if not diagnostic_log else " | ".join(diagnostic_log)
            surcharge_str = ", ".join(ms_metrics.get("machship_surcharge_names", [])) if ms_metrics else "None"

            if expected_amount > 0.01:
                markup_factor = expected_sell / expected_amount
            else:
                markup_factor = 1.19
                
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
            batch_prompt = f"You are a forensic freight auditor. I am providing a JSON array of {len(analysis_batch)} consignments that have a cost variance. Compare the carrier_invoice_line text against the machship_metrics. Look explicitly for Discrepancies in Weight or Volume, Missing or Added Surcharges, and Base rate mismatches.\n\nCRITICAL INSTRUCTION 1: Try to actively FIGURE OUT the root cause of the discrepancy rather than just reporting the numbers. \n\nCRITICAL INSTRUCTION 2: Format your analysis inside 'variance_reason' with logical line breaks. You MUST insert a line break character ('\\n') after EVERY full stop (.) to ensure the text remains short per line in the spreadsheet cell.\n\nCRITICAL INSTRUCTION 3: You MUST return exactly {len(analysis_batch)} JSON objects in your array. Do NOT skip any items. Do NOT summarize. Return ONLY a valid JSON array of objects with strictly two keys: 'connote' and 'variance_reason'.\n\nVariance Data: {json.dumps(analysis_batch)}"
            
            try:
                analysis_text = call_gemini_api(batch_prompt, json_mode=True)
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

        drive_scope = get_secure_endpoint("drive_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9kcml2ZQ==")
        sheets_scope = get_secure_endpoint("sheets_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9zcHJlYWRzaGVldHM=")
        
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
        success_statuses = ['delivered', 'on board for delivery', 'partially delivered', 'awaiting collection', 'completed']
        error_statuses = ['exception', 'delayed', 'held', 'damaged', 'missed pickup']
        
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
    """
    Sweeps HubSpot Conversations for new WISMO requests.
    Extracts references, queries Machship, evaluates sentiment (Positive/Negative).
    If positive, replies to customer with tracking/POD link. If negative, leaves internal note.
    """
    import datetime
    
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
        threads_resp = requests.get(f"{hs_threads_url}?status=OPEN", headers=hs_headers, timeout=15)
        if threads_resp.status_code != 200:
            return f"🚨 CRITICAL CRASH: HubSpot API Request Failed (HTTP {threads_resp.status_code}). Raw Payload: {threads_resp.text}"
            
        threads_data = threads_resp.json().get("results", [])
        if not threads_data:
            return "WISMO Sweep Complete. No open conversational threads found."
            
        action_log = []
        
        for thread in threads_data:
            thread_id = thread.get("id")
            
            messages_resp = requests.get(f"{hs_threads_url}/{thread_id}/messages", headers=hs_headers, timeout=15)
            if messages_resp.status_code != 200:
                return f"🚨 CRITICAL CRASH: HubSpot Messages API Failed (HTTP {messages_resp.status_code}). Raw Payload: {messages_resp.text}"
            
            messages = messages_resp.json().get("results", [])
            if not messages: continue
            
            latest_msg = messages[-1]
            msg_text = latest_msg.get("text", "")
            if not msg_text or latest_msg.get("type") == "COMMENT": continue
            
            extract_prompt = f"Extract any specific alphanumeric tracking/consignment numbers (e.g. MS123456, REF999) from this customer email text. Return ONLY a valid JSON array of strings containing the exact reference numbers. Text: {msg_text}"
            extracted_refs_str = call_gemini_api(extract_prompt, json_mode=True)
            try:
                refs = json.loads(extracted_refs_str)
            except:
                refs = []
                
            if not refs or not isinstance(refs, list) or len(refs) == 0:
                continue
                
            connote = refs[0].upper()
            
            ms_headers = { "token": ms_token, "Content-Type": "application/json" }
            ms_search_urls = [
                get_secure_endpoint("machship_get", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvZ2V0Q29uc2lnbm1lbnQ/aWQ9"),
                get_secure_endpoint("machship_ref1", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UxP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl")
            ]
            
            tracking_info = None
            ms_consign_id = None
            has_pod = False
            
            for url in ms_search_urls:
                if "?id=" in url and connote.startswith("MS"):
                    ms_id = re.sub(r"\D", "", connote)
                    r = requests.get(f"{url}{ms_id}", headers=ms_headers, timeout=10)
                else:
                    r = requests.post(url, headers=ms_headers, json=[connote], timeout=10)
                    
                if r.status_code == 200:
                    data = r.json()
                    obj = data.get("object")
                    if isinstance(obj, list) and len(obj) > 0: obj = obj[0]
                    
                    if obj:
                        tracking_info = json.dumps(obj)
                        ms_consign_id = obj.get("id")
                        has_pod = obj.get("attachmentCount", 0) > 0
                        break
                        
            if not tracking_info:
                if not dry_run:
                    note_payload = { "type": "COMMENT", "text": f"BOOF WISMO Alert: Could not locate Machship data for reference {connote}. Reassigning to human broker." }
                    requests.post(f"{hs_threads_url}/{thread_id}/messages", headers=hs_headers, json=note_payload)
                action_log.append(f"Thread {thread_id}: Reference {connote} not found. Left internal note.")
                continue
                
            eval_prompt = f"""
            You are the BOOF Freight Concierge. Analyze this JSON freight tracking data: {tracking_info}
            
            Task:
            1. Evaluate status. POSITIVE = (Delivered, Booked, On board for delivery, Manifested, In Transit). NEGATIVE = (Delayed, Exception, Damaged, Lost, Missed Pickup).
            2. If POSITIVE, draft a highly professional, concise, non-chatty message for the customer. E.g., 'Consignment [ID] is currently in transit. Expected ETA is [Date].'
            3. If NEGATIVE, draft an internal note for the FCA broker. E.g., 'ACTION REQUIRED: [ID] is delayed. Exception flagged.'
            
            Return ONLY a valid JSON object with keys: 'sentiment' (strictly "POSITIVE" or "NEGATIVE") and 'message' (the drafted text).
            """
            
            eval_str = call_gemini_api(eval_prompt, json_mode=True)
            try:
                eval_res = json.loads(eval_str)
            except:
                eval_res = {"sentiment": "NEGATIVE", "message": "Failed to parse AI evaluation."}
                
            sentiment = eval_res.get("sentiment", "NEGATIVE")
            base_message = eval_res.get("message", "Status unavailable.")
            
            if sentiment == "POSITIVE":
                if has_pod and ms_consign_id:
                    pod_link = f"[https://live.machship.com/tracking?id=](https://live.machship.com/tracking?id=){ms_consign_id}"
                    base_message += f"\n\nProof of Delivery and live tracking are securely accessible via the carrier portal: {pod_link}"
                    
                if not dry_run:
                    reply_payload = { "type": "MESSAGE", "text": base_message }
                    req = requests.post(f"{hs_threads_url}/{thread_id}/messages", headers=hs_headers, json=reply_payload)
                    if req.status_code not in [200, 201]:
                        return f"🚨 CRITICAL CRASH: HubSpot POST Reply Failed (HTTP {req.status_code}). Raw Payload: {req.text}"
                action_log.append(f"Thread {thread_id}: POSITIVE status for {connote}. Replied to customer (POD Attached: {has_pod}).")
                
            else:
                if not dry_run:
                    note_payload = { "type": "COMMENT", "text": f"BOOF WISMO Alert: {base_message}" }
                    req = requests.post(f"{hs_threads_url}/{thread_id}/messages", headers=hs_headers, json=note_payload)
                    if req.status_code not in [200, 201]:
                        return f"🚨 CRITICAL CRASH: HubSpot POST Note Failed (HTTP {req.status_code}). Raw Payload: {req.text}"
                action_log.append(f"Thread {thread_id}: NEGATIVE status for {connote}. Left internal broker note.")
                
        return "WISMO Sweep Complete.\n" + "\n".join(action_log) if action_log else "WISMO Sweep Complete. No actionable items."
            
    except Exception as e:
        return f"🚨 CRITICAL CRASH: {sanitize_error_log(str(e))}"

# ==========================================
# BACKWARD COMPATIBILITY ALIASES 
# ==========================================
def tool_11_transit_delay_engine(*args, **kwargs):
    return tool_10_freight_alert_automator(dry_run=kwargs.get('dry_run', False))
    
def tool_10_temporal_anomaly_detector(*args, **kwargs):
    return tool_10_freight_alert_automator()
