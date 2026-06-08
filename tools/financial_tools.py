import io
import re
import json
import time
import requests
import datetime
import pandas as pd
import streamlit as st
from google.oauth2 import service_account
from googleapiclient.discovery import build

from tools.core_utils import (
    get_secure_endpoint,
    sanitize_error_log,
    call_gemini_api,
    vision_bridge_pdf_to_csv,
    get_xero_token,
    get_cartoncloud_token
)

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
            active_contacts = [c for c in contacts if c.get("ContactStatus") != "ARCHIVED"]
            
            def get_outstanding(c):
                try:
                    return float(c.get("Balances", {}).get("AccountsReceivable", {}).get("Outstanding", 0.0))
                except (ValueError, TypeError):
                    return 0.0
                    
            active_contacts.sort(key=get_outstanding, reverse=True)
            
            results_summary = []
            for contact in active_contacts[:5]:
                name = contact.get("Name", "Unknown")
                status = contact.get("ContactStatus", "Unknown")
                balances = contact.get("Balances", {}).get("AccountsReceivable", {})
                outstanding = balances.get("Outstanding", 0.00)
                overdue = balances.get("Overdue", 0.00)
                results_summary.append(f"✅ Xero Record: {name} | Status: {status} | Outstanding: ${outstanding} | Overdue: ${overdue}")
            
            raw_data = json.dumps(active_contacts[:5], indent=2)
            summary_string = "\n".join(results_summary)
            return f"{summary_string}\n\n**Raw Data Available to AI:**\n```json\n{raw_data}\n```"
        else:
            return f"No contact found in Xero matching '{contact_name}' or its primary keyword."
            
    except Exception as e:
        return f"🚨 Xero API Error: {sanitize_error_log(str(e))}"

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
# TOOL 17: KERMIT (CartonCloud Machship Invoice Reconciliation Tool)
# ==========================================
def tool_17_kermit_reconciliation_engine(start_date: str, end_date: str, customer_name: str = "Rhino") -> str:
    import datetime
    import pandas as pd
    import requests
    import streamlit as st
    import json
    import time
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from tools.core_utils import get_secure_endpoint, sanitize_error_log, get_cartoncloud_token
    
    def parse_flexible_date(date_string: str) -> datetime.date:
        import re
        clean_str = re.sub(r'(?i)(st|nd|rd|th)', '', str(date_string)).strip()
        formats = [
            "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", 
            "%d %b %Y", "%d %B %Y", "%Y/%m/%d", "%B %d %Y", "%b %d %Y"
        ]
        for fmt in formats:
            try:
                return datetime.datetime.strptime(clean_str, fmt).date()
            except ValueError:
                continue
        return datetime.datetime.now().date()

    try:
        start_dt = parse_flexible_date(start_date)
        end_dt = parse_flexible_date(end_date)
    except Exception as e:
        return f"CRITICAL CRASH: Date Engine Failure. {sanitize_error_log(str(e))}"
        
    diagnostic_logs = []
    
    cc_tenant_id = st.secrets["cartoncloud"]["tenant_id"].strip()
    cc_base_url = get_secure_endpoint("cartoncloud_base", "aHR0cHM6Ly9hcGkuY2FydG9uY2xvdWQuY29t")
    cc_token = get_cartoncloud_token()
    
    if "Error" in cc_token:
        return f"CRITICAL CRASH: CartonCloud Authentication Failure. {cc_token}"
        
    cc_headers = {
        "Accept-Version": "1",
        "Authorization": f"Bearer {cc_token}",
        "Content-Type": "application/json"
    }
    
    # ---------------------------------------------------------
    # INITIATE ASYNCHRONOUS BULK CHARGES REPORT
    # ---------------------------------------------------------
    report_run_id = None
    try:
        report_payload = {
            "type": "BULK_CHARGES",
            "parameters": {
                "pageSize": 100,
                "dateFilter": "date_activity",
                "fromDate": start_dt.strftime("%Y-%m-%d"),
                "toDate": end_dt.strftime("%Y-%m-%d"),
                "chargeClasses": ["SALE_ORDER"]
            }
        }
        report_init_url = f"{cc_base_url}/tenants/{cc_tenant_id}/report-runs"
        report_init_resp = requests.post(report_init_url, headers=cc_headers, json=report_payload, timeout=15)
        
        if report_init_resp.status_code == 201:
            report_run_id = report_init_resp.json().get("id")
        else:
            diagnostic_logs.append(f"Report Init Failure (HTTP {report_init_resp.status_code})")
    except Exception as e:
        diagnostic_logs.append(f"Report Trigger Exception: {str(e)}")

    # ---------------------------------------------------------
    # METADATA SWEEP: OUTBOUND ORDERS
    # ---------------------------------------------------------
    raw_orders = []
    
    # 2-Stage Fortress Sweep (20 pages = 2,000 orders to ensure deep historical reach)
    for page in range(1, 21): 
        try:
            # Pagination placed natively in the URL to satisfy strict endpoint logic
            search_url = f"{cc_base_url}/tenants/{cc_tenant_id}/outbound-orders/search?page={page}&size=100"
            
            # Stage 1: Native Filter strictly nested within the mandated AndCondition structure
            search_payload = {
                "condition": {
                    "type": "AndCondition",
                    "conditions": [
                        {
                            "type": "TextComparisonCondition",
                            "field": { "type": "JsonField", "pointer": "/customer/name" },
                            "value": { "type": "ValueField", "value": customer_name },
                            "method": "CONTAINS"
                        }
                    ]
                },
                "sort": [{"field": {"type": "JsonField", "pointer": "/id"}, "direction": "DESC"}]
            }
            
            resp = requests.post(search_url, headers=cc_headers, json=search_payload, timeout=15)
            
            if resp.status_code == 200:
                page_data = resp.json()
                if not page_data: break
                raw_orders.extend(page_data)
            else:
                diagnostic_logs.append(f"Native Filter Rejected (HTTP {resp.status_code}). Executing Fallback Sweep on page {page}.")
                # Stage 2: Permissive Date Filter fallback strictly nested within the mandated AndCondition structure
                fallback_payload = {
                    "condition": {
                        "type": "AndCondition",
                        "conditions": [
                            {
                                "type": "DateTimeComparisonCondition",
                                "field": { "type": "JsonField", "pointer": "/timestamps/created/time" },
                                "value": { "type": "ValueField", "value": "2015-01-01T00:00:00+10:00" },
                                "method": "GREATER_THAN"
                            }
                        ]
                    },
                    "sort": [{"field": {"type": "JsonField", "pointer": "/id"}, "direction": "DESC"}]
                }
                resp_fb = requests.post(search_url, headers=cc_headers, json=fallback_payload, timeout=15)
                
                if resp_fb.status_code == 200:
                    page_data = resp_fb.json()
                    if not page_data: break
                    raw_orders.extend(page_data)
                else:
                    diagnostic_logs.append(f"CartonCloud Search HTTP Error: {resp_fb.status_code} - {resp_fb.text}")
                    break
                    
        except Exception as e:
            diagnostic_logs.append(f"CartonCloud Sweep Crash: {sanitize_error_log(str(e))}")
            break

    # ---------------------------------------------------------
    # POLL AND EXTRACT BULK CHARGES
    # ---------------------------------------------------------
    warehouse_costs_map = {}
    
    if report_run_id:
        poll_status = "IN_PROCESS"
        backoff_intervals = [10, 20, 40]
        attempt = 0
        
        while poll_status == "IN_PROCESS" and attempt < len(backoff_intervals):
            time.sleep(backoff_intervals[attempt])
            try:
                poll_resp = requests.get(f"{cc_base_url}/tenants/{cc_tenant_id}/report-runs/{report_run_id}", headers=cc_headers, timeout=15)
                if poll_resp.status_code == 200:
                    poll_data = poll_resp.json()
                    poll_status = poll_data.get("status", "FAILED")
                    
                    if poll_status == "SUCCESS":
                        page = 1
                        while True:
                            page_url = f"{cc_base_url}/tenants/{cc_tenant_id}/report-runs/{report_run_id}?page={page}&size=100"
                            page_resp = requests.get(page_url, headers=cc_headers, timeout=15)
                            
                            if page_resp.status_code == 200:
                                p_data = page_resp.json()
                                items = p_data.get("items", [])
                                for item in items:
                                    sale_order_id = item.get("parentUuid")
                                    charge_val = float(item.get("charge", 0.0))
                                    if sale_order_id:
                                        warehouse_costs_map[sale_order_id] = warehouse_costs_map.get(sale_order_id, 0.0) + charge_val
                                
                                total_pages = int(page_resp.headers.get("Total-Pages", 1))
                                if page >= total_pages: break
                                page += 1
                            else:
                                break
            except Exception as e:
                diagnostic_logs.append(f"Report Polling Crash: {str(e)}")
            attempt += 1

    # ---------------------------------------------------------
    # DATA AGGREGATION & MATRIX GENERATION
    # ---------------------------------------------------------
    matrix_data = []
    
    for order in raw_orders:
        o_customer = order.get("customer", {}).get("name", "")
        if customer_name.lower() not in o_customer.lower():
            continue
            
        timestamps = order.get("timestamps", {})
        o_date_str = timestamps.get("dispatched", {}).get("time") or timestamps.get("created", {}).get("time")
        
        if not o_date_str: continue
        
        try:
            o_date = datetime.datetime.strptime(o_date_str[:10], "%Y-%m-%d").date()
        except Exception:
            continue
            
        if not (start_dt <= o_date <= end_dt):
            continue
            
        cust_ref = order.get("references", {}).get("customer", "")
        order_uuid = order.get("id", "")
        
        cc_items = order.get("items", [])
        cc_products_list = []
        cc_total_qty = 0.0
        
        for item in cc_items:
            prod_code = item.get("details", {}).get("product", {}).get("references", {}).get("code", "Unknown")
            qty = float(item.get("measures", {}).get("quantity", 0))
            cc_total_qty += qty
            cc_products_list.append(f"{qty}x {prod_code}")
            
        cc_products_str = ", ".join(cc_products_list)
        mapped_warehouse_cost = float(warehouse_costs_map.get(order_uuid, 0.0))
        
        matrix_data.append({
            "Date": o_date_str[:10],
            "Customer Reference": cust_ref,
            "CC Products": cc_products_str,
            "CC Total Qty": cc_total_qty,
            "Machship Consignment": "",
            "Machship Carrier Connote": "",
            "From Details": "",
            "To Details": "",
            "To Contact": "",
            "Total Item Count": 0,
            "Total Weight": 0.0,
            "Warehouse Cost": mapped_warehouse_cost,
            "Machship Sell": 0.0
        })

    if not matrix_data:
        log_output = " | ".join(diagnostic_logs) if diagnostic_logs else "Clean"
        return f"KERMIT Sweep Complete. No valid orders found for {customer_name} between {start_dt.strftime('%Y-%m-%d')} and {end_dt.strftime('%Y-%m-%d')}. Diagnostics: {log_output}"

    ms_token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
    ms_headers = { "token": ms_token, "Content-Type": "application/json" }
    
    ms_urls = [
        get_secure_endpoint("machship_ref1", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UxP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl"),
        get_secure_endpoint("machship_ref2", "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UyP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl")
    ]
    
    for row in matrix_data:
        ref = row["Customer Reference"]
        if not ref: continue
        
        found = False
        for url in ms_urls:
            if found: break
            try:
                resp = requests.post(url, headers=ms_headers, json=[str(ref)], timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    obj_list = data.get("object")
                    if obj_list and len(obj_list) > 0:
                        consignment = obj_list[0]
                        c_total = consignment.get("consignmentTotal", {})
                        
                        sell = c_total.get("totalSellPrice") or c_total.get("totalSellBeforeTax") or c_total.get("totalSell") or 0.0
                        
                        from_loc = consignment.get("fromLocation", {})
                        from_suburb = from_loc.get("suburb", "")
                        from_state = from_loc.get("state", {}).get("abbreviation", "") if isinstance(from_loc.get("state"), dict) else from_loc.get("state", "")
                        from_str = f"{consignment.get('fromName', '')} | {consignment.get('fromAddressLine1', '')} {from_suburb} {from_state}".strip()
                        
                        to_loc = consignment.get("toLocation", {})
                        to_suburb = to_loc.get("suburb", "")
                        to_state = to_loc.get("state", {}).get("abbreviation", "") if isinstance(to_loc.get("state"), dict) else to_loc.get("state", "")
                        to_str = f"{consignment.get('toName', '')} | {consignment.get('toAddressLine1', '')} {to_suburb} {to_state}".strip()
                        
                        to_contact_str = f"{consignment.get('toContact', '')} / {consignment.get('toPhone', '')} / {consignment.get('toEmail', '')}".strip(" /")
                        
                        ms_items = consignment.get("items", [])
                            
                        row["Machship Consignment"] = consignment.get("consignmentNumber", "")
                        row["Machship Carrier Connote"] = consignment.get("carrierConsignmentId", "")
                        row["From Details"] = from_str
                        row["To Details"] = to_str
                        row["To Contact"] = to_contact_str
                        row["Total Item Count"] = int(consignment.get("totalItemCount", len(ms_items)))
                        row["Total Weight"] = float(consignment.get("weight", consignment.get("totalWeight", 0.0)))
                        
                        row["Machship Sell"] = float(sell)
                        found = True
            except Exception:
                pass

    df = pd.DataFrame(matrix_data)
    
    # Final column ordering enforcement
    col_order = [
        "Date", "Customer Reference", "CC Products", "CC Total Qty", "Machship Consignment", 
        "Machship Carrier Connote", "From Details", "To Details", "To Contact", 
        "Total Item Count", "Total Weight", "Warehouse Cost", "Machship Sell"
    ]
    
    try:
        df = df[col_order]
    except KeyError:
        pass
    
    try:
        drive_scope = get_secure_endpoint("drive_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9kcml2ZQ==")
        sheets_scope = get_secure_endpoint("sheets_scope", "aHR0cHM6Ly93d3cuZ29vZ2xlYXBpcy5jb20vYXV0aC9zcHJlYWRzaGVldHM=")
        
        credentials_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            credentials_dict, scopes=[drive_scope, sheets_scope]
        )

        sheets_service = build("sheets", "v4", credentials=creds)
        drive_service = build("drive", "v3", credentials=creds)

        parent_folder_id = "1U8PYxUZMfJql0AYnhc0izJpI0FqveeFR"
        timestamp_str = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        sheet_title = f"KERMIT Analysis - {customer_name} ({start_dt.strftime('%d-%b-%y')} to {end_dt.strftime('%d-%b-%y')}) - {timestamp_str}"

        file_metadata = {
            'name': sheet_title,
            'mimeType': 'application/vnd.google-apps.spreadsheet',
            'parents': [parent_folder_id]
        }
        
        sheet_file = drive_service.files().create(body=file_metadata, fields='id', supportsAllDrives=True).execute()
        spreadsheet_id = sheet_file.get('id')

        headers_list = df.columns.tolist()
        raw_values = df.values.tolist()
        
        scrubbed_values = [headers_list]
        for row in raw_values:
            clean_row = ["" if pd.isna(item) else str(item) for item in row]
            scrubbed_values.append(clean_row)

        try:
            sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
            sheet_id = sheet_metadata['sheets'][0]['properties']['sheetId']
            
            requests_body = {
                "requests": [{
                    "updateSheetProperties": {
                        "properties": {
                            "sheetId": sheet_id,
                            "gridProperties": {
                                "rowCount": max(1000, len(scrubbed_values) + 100),
                                "columnCount": max(35, len(headers_list) + 5)
                            }
                        },
                        "fields": "gridProperties(rowCount,columnCount)"
                    }
                }]
            }
            sheets_service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=requests_body).execute()
        except Exception:
            pass

        body = { "values": scrubbed_values }
        sheets_service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range="Sheet1!A1", valueInputOption="USER_ENTERED", body=body
        ).execute()

        human_email = st.session_state.get("user_email", "")
        if human_email:
            try:
                permission = {"type": "user", "role": "writer", "emailAddress": human_email}
                drive_service.permissions().create(fileId=spreadsheet_id, body=permission, fields="id", supportsAllDrives=True).execute()
            except Exception:
                pass 

        sheet_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        log_str = " | ".join(diagnostic_logs)
        return f"SUCCESS: KERMIT module executed. Processed {len(matrix_data)} records for {customer_name}. \nDiagnostics: {log_str if log_str else 'Clean'}\n\nView Financial Matrix: {sheet_url}"

    except Exception as e:
        return f"CRITICAL CRASH (KERMIT): {sanitize_error_log(str(e))}"
    finally:
        try:
            del creds, sheets_service, drive_service
        except NameError:
            pass
