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
