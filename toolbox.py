import requests
import base64
import re
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
# TOOL 1: XERO CONTACT SEARCH
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
            return f"Found Contact: {contact.get('Name')}. Status: {contact.get('ContactStatus')}."
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
                return f"✅ Machship Record (MS): Carrier: {carrier} | Status: {status}."
            else:
                return f"Could not find MS consignment '{connote_number}'. RAW: {data.get('errors')}"
        else:
            return f"API Error (MS Search): {response.text}"

    # PATH B: Carrier ID & Reference Hunt
    headers["Content-Type"] = "application/json"
    
    # FIX: Capitalized Keys (CarrierConsignmentIds and References) to satisfy Machship's strict server rules
    search_routes = [
        ("Carrier ID", "https://live.machship.com/apiv2/consignments/returnConsignmentsByCarrierConsignmentId", "CarrierConsignmentIds"),
        ("Reference 1", "https://live.machship.com/apiv2/consignments/returnConsignmentsByReference1", "References"),
        ("Reference 2", "https://live.machship.com/apiv2/consignments/returnConsignmentsByReference2", "References")
    ]

    error_log = []

    for search_type, url, payload_key in search_routes:
        payload = { 
            payload_key: [connote_number]
        }
        
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code == 200:
            data = response.json()
            # If Machship successfully found it:
            if data.get("object") and len(data["object"]) > 0:
                consignment = data["object"][0]
                carrier = consignment.get("carrier", {}).get("name") or consignment.get("carrier", {}).get("abbreviation") or "Carrier Not Assigned"
                status = consignment.get("status", {}).get("name", "Unknown Status")
                return f"✅ Machship Record (Found via {search_type}): Carrier: {carrier} | Status: {status}."
            else:
                # Still saving errors just in case it fails again
                error_log.append(f"{search_type} API Reply: {data.get('errors')}")
        else:
            error_log.append(f"{search_type} HTTP Error: {response.text}")

    # If all 3 fail, print the exact error log to the screen
    return f"Failed to find '{connote_number}'. Machship's internal response:\n" + "\n".join(error_log)
