import requests
import base64
import re
import json
import streamlit as st

# ==========================================
# XERO CONNECTION HANDSHAKE
# ==========================================
def get_xero_token():
    client_id = st.secrets["XERO_CLIENT_ID"]
    client_secret = st.secrets["XERO_CLIENT_SECRET"]
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    url = "https://identity.xero.com/connect/token"
    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = { "grant_type": "client_credentials" }
    response = requests.post(url, headers=headers, data=data)
    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        return f"Error connecting to Xero: {response.text}"

# ==========================================
# TOOL 1: XERO FINANCIAL SEARCH
# ==========================================
def search_xero_contact(contact_name):
    token = get_xero_token()
    if "Error" in token: return token 
    url = f'https://api.xero.com/api.xro/2.0/Contacts?where=Name.Contains("{contact_name}")'
    headers = { "Authorization": f"Bearer {token}", "Accept": "application/json" }
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        if data.get("Contacts"):
            contact = data["Contacts"][0]
            name = contact.get("Name", "Unknown")
            status = contact.get("ContactStatus", "Unknown")
            
            # Dig into Xero's financial balances
            balances = contact.get("Balances", {}).get("AccountsReceivable", {})
            outstanding = balances.get("Outstanding", 0.00)
            overdue = balances.get("Overdue", 0.00)
            
            # Format the raw dictionary for the AI
            raw_data = json.dumps(contact, indent=2)
            
            return f"✅ Xero Record: {name} | Status: {status} | Total Outstanding: ${outstanding} | Overdue: ${overdue}\n\n**Raw Data Available to AI:**\n```json\n{raw_data}\n```"
        else:
            return f"No contact found in Xero matching '{contact_name}'."
    else:
        return f"Xero API Error: {response.text}"

# ==========================================
# TOOL 2: UNRESTRICTED MACHSHIP SEARCH
# ==========================================
def search_machship_connote(connote_number):
    token = st.secrets["MACHSHIP_API_TOKEN"]
    connote_number = connote_number.strip().upper()
    headers = { "token": token, "Accept": "application/json" }

    # PATH A: It is an internal Machship number
    if connote_number.startswith("MS"):
        ms_id = re.sub(r"\D", "", connote_number)
        url = f"https://live.machship.com/apiv2/consignments/getConsignment?id={ms_id}"
        response = requests.get(url, headers=headers)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("object"):
                consignment = data["object"]
                carrier = consignment.get("carrier", {}).get("name") or consignment.get("carrier", {}).get("abbreviation") or "Carrier Not Assigned"
                status = consignment.get("status", {}).get("name", "Unknown Status")
                
                raw_data = json.dumps(consignment, indent=2)
                return f"✅ Machship Record (MS): Carrier: {carrier} | Status: {status}\n\n**Raw Data Available to AI:**\n```json\n{raw_data}\n```"
            else:
                return f"Could not find MS consignment '{connote_number}'."
        else:
            return f"API Error (MS Search): {response.text}"

    # PATH B: Carrier ID & Reference Hunt
    headers["Content-Type"] = "application/json"
    
    search_routes = [
        ("Carrier ID", "https://live.machship.com/apiv2/consignments/returnConsignmentsByCarrierConsignmentId?includeChildCompanies=true"),
        ("Reference 1", "https://live.machship.com/apiv2/consignments/returnConsignmentsByReference1?includeChildCompanies=true"),
        ("Reference 2", "https://live.machship.com/apiv2/consignments/returnConsignmentsByReference2?includeChildCompanies=true")
    ]

    payload = [connote_number]

    for search_type, url in search_routes:
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("object") and len(data["object"]) > 0:
                consignment = data["object"][0]
                carrier = consignment.get("carrier", {}).get("name") or consignment.get("carrier", {}).get("abbreviation") or "Carrier Not Assigned"
                status = consignment.get("status", {}).get("name", "Unknown Status")
                
                raw_data = json.dumps(consignment, indent=2)
                return f"✅ Machship Record (Found via {search_type}): Carrier: {carrier} | Status: {status}\n\n**Raw Data Available to AI:**\n```json\n{raw_data}\n```"

    return f"Failed to find '{connote_number}' in Machship."
# ==========================================
# TOOL 3: TRANSVIRTUAL CONSIGNMENT SEARCH
# ==========================================
def search_transvirtual_connote(connote_number):
    import json
    import requests
    import streamlit as st
    
    token = st.secrets["TRANSVIRTUAL_API_KEY"]
    connote_number = connote_number.strip().upper()
    
    headers = {
        "Authorization": token, 
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    try:
        # STEP 1: The Search Stub (Find the internal ID)
        url_search = "https://api.transvirtual.com.au/api/Consignment/Search"
        response_search = requests.post(url_search, headers=headers, json={"ConsignmentNumber": connote_number})
        
        internal_id = None
        if response_search.status_code == 200:
            internal_id = response_search.json().get("Data", {}).get("Id")
            
        # STEP 2: Fetch Consignment Details
        url_query = "https://api.transvirtual.com.au/api/ConsignmentQuery"
        response_query = requests.post(url_query, headers=headers, json={"ConsignmentNumber": connote_number})
        full_data = response_query.json().get("Data", {}) if response_query.status_code == 200 else {}
        
        # STEP 3: The Ultimate Tracking Skeleton Key
        tracking_log = []
        tracking_data = None
        
        test_tracking_routes = [
            ("GET /ConsignmentStatus", f"https://api.transvirtual.com.au/api/ConsignmentStatus?ConsignmentNumber={connote_number}", None),
            ("GET /Tracking (Query)", f"https://api.transvirtual.com.au/api/Tracking?ConsignmentNumber={connote_number}", None),
            ("GET /Tracking (ID)", f"https://api.transvirtual.com.au/api/Tracking/{internal_id}" if internal_id else "SKIP", None),
            ("POST /ConsignmentStatus (Array)", "https://api.transvirtual.com.au/api/ConsignmentStatus", [connote_number]),
            ("POST /Tracking (Array)", "https://api.transvirtual.com.au/api/Tracking", [connote_number]),
            ("POST /Tracking (Number)", "https://api.transvirtual.com.au/api/Tracking", {"Number": connote_number})
        ]
        
        for test_name, url, test_payload in test_tracking_routes:
            if url == "SKIP":
                continue
            
            if test_payload is not None:
                resp = requests.post(url, headers=headers, json=test_payload)
            else:
                resp = requests.get(url, headers=headers)
                
            # Check for a successful hit that DOESN'T contain our previous error message
            if resp.status_code == 200 and "Missing consignment number" not in resp.text:
                tracking_data = resp.json()
                tracking_log.append(f"✅ Hit {test_name}")
                break
            else:
                tracking_log.append(f"❌ {test_name} -> HTTP {resp.status_code} | {resp.text[:50]}")
        
        # Combine the Data for Digital Marsh
        combined_matrix = {
            "ConsignmentDetails": full_data,
            "TrackingScans": tracking_data if tracking_data else "Failed tracking X-Ray: " + " | ".join(tracking_log)
        }
        
        raw_matrix = json.dumps(combined_matrix, indent=2)
        
        return f"✅ Transvirtual Record: {connote_number}\n\n**Raw Data Available to AI:**\n```json\n{raw_matrix}\n```"
            
    except Exception as e:
        return f"Transvirtual API Crash: {str(e)}"
