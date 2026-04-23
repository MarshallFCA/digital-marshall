import requests
import base64
import re
import streamlit as st

# ==========================================
# XERO CONNECTION HANDSHAKE
# ==========================================
def get_xero_token():
    """Silently logs into Xero using the vault credentials to get a temporary access token."""
    client_id = st.secrets["XERO_CLIENT_ID"]
    client_secret = st.secrets["XERO_CLIENT_SECRET"]
    
    credentials = f"{client_id}:{client_secret}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    
    url = "https://identity.xero.com/connect/token"
    headers = {
        "Authorization": f"Basic {encoded_credentials}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "client_credentials"
    }
    
    response = requests.post(url, headers=headers, data=data)
    if response.status_code == 200:
        return response.json()["access_token"]
    else:
        return f"Error connecting to Xero: {response.text}"

# ==========================================
# TOOL 1: XERO CONTACT SEARCH
# ==========================================
def search_xero_contact(contact_name):
    """Searches Xero for a specific contact name to see if they exist and are active."""
    token = get_xero_token()
    
    if "Error" in token:
        return token 
        
    url = f'https://api.xero.com/api.xro/2.0/Contacts?where=Name.Contains("{contact_name}")'
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
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
# TOOL 2: SMART MACHSHIP SEARCH
# ==========================================
def search_machship_connote(connote_number):
    """Smart search: checks MS internal numbers first, then Carrier IDs."""
    token = st.secrets["MACHSHIP_API_TOKEN"]
    connote_number = connote_number.strip().upper()
    
    headers = {
        "token": token,
        "Accept": "application/json"
    }

    # PATH A: It is an internal Machship number (Starts with MS)
    if connote_number.startswith("MS"):
        # Strip away the "MS" to get just the ID number Machship's server wants
        ms_id = re.sub(r"\D", "", connote_number)
        url = f"https://live.machship.com/apiv2/consignments/getConsignment?id={ms_id}"
        
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if data.get("object"):
                consignment = data["object"]
                carrier = consignment.get("carrier", {}).get("name", "Unknown Carrier")
                status = consignment.get("status", {}).get("name", "Unknown Status")
                return f"✅ Machship Record (MS): Carrier: {carrier} | Status: {status}."
            else:
                return f"Could not find MS consignment '{connote_number}'."
        else:
            return f"API Error (MS Search): {response.text}"

    # PATH B: It is a Carrier Consignment Number (e.g. FCU000069)
    else:
        url = "https://live.machship.com/apiv2/consignments/returnConsignmentsByCarrierConsignmentId"
        headers["Content-Type"] = "application/json"
        payload = {
            "carrierConsignmentIds": [connote_number]
        }
        
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code == 200:
            data = response.json()
            if data.get("object") and len(data["object"]) > 0:
                consignment = data["object"][0]
                carrier = consignment.get("carrier", {}).get("name", "Unknown Carrier")
                status = consignment.get("status", {}).get("name", "Unknown Status")
                return f"✅ Machship Record (Carrier ID): Carrier: {carrier} | Status: {status}."
            else:
                return f"Could not find Carrier Consignment '{connote_number}'."
        else:
            return f"API Error (Carrier Search): {response.text}"
