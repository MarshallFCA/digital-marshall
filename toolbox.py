if code_match:
            json_str = code_match.group(1).strip()
        else:
            json_str = response_text.strip()

        try:
            invoice_items = json.loads(json_str)
        except json.JSONDecodeError as e:
            return f"Error: Failed to parse JSON payload. JSONDecodeError: {str(e)}\n\nRaw Fragment:\n{json_str}"

        # Extract headers from the invoice to assist the second AI prompt
        invoice_header_sample = raw_invoice_text.split('\n')[0][:500] if raw_invoice_text else "N/A"

        # Setup API Telemetry & Auth for Machship Loop
        ms_token = st.secrets["machship"]["MACHSHIP_API_TOKEN"]
        ms_headers = { "token": ms_token, "Content-Type": "application/json" }
        reconciliation_data = []
        analysis_batch = []

        # CLEAN API URLS
        b64_urls = [
            "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlDYXJyaWVyQ29uc2lnbm1lbnRJZD9pbmNsdWRlQ2hpbGRDb21wYW5pZXM9dHJ1ZQ==",
            "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UxP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl",
            "aHR0cHM6Ly9saXZlLm1hY2hzaGlwLmNvbS9hcGl2Mi9jb25zaWdubWVudHMvcmV0dXJuQ29uc2lnbm1lbnRzQnlSZWZlcmVuY2UyP2luY2x1ZGVDaGlsZENvbXBhbmllcz10cnVl"
        ]
        search_urls = [base64.b64decode(u).decode() for u in b64_urls]

        # Ensure we are iterating over a list
        if not isinstance(invoice_items, list):
            invoice_items = [invoice_items] if isinstance(invoice_items, dict) else []

        # Process each extracted item
        for item in invoice_items:
            connote = item.get("connote", "") or ""
            raw_invoice_line = item.get("raw_invoice_line", "N/A")
            
            raw_billed = item.get("billed_amount", 0.0)
            try:
                billed_amount = float(raw_billed) if raw_billed is not None else 0.0
            except (ValueError, TypeError):
                billed_amount = 0.0

            expected_amount = 0.0
            diagnostic_log = []
            found = False
            ms_metrics = {}

            if not connote:
                diagnostic_log.append("Missing connote parameter from extracted payload.")
            else:
                for url in search_urls:
                    try:
                        resp = requests.post(url, headers=ms_headers, json=[connote], timeout=15)
                        if resp.status_code == 200:
                            data = resp.json()
                            if data.get("object") and len(data["object"]) > 0:
                                consignment = data["object"][0]
                                c_total = consignment.get("consignmentTotal") or {}
                                
                                # Extract deeper metrics for natural language analysis
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
                                    "machship_fuel_levy": c_total.get("costFuelLevyPrice", 0),
                                    "machship_surcharges_total": c_total.get("totalConsignmentCarrierSurchargesCostPrice", 0),
                                    "machship_surcharge_names": surcharge_names,
                                    "machship_items": item_summary
                                }
                                
                                # STRICT BUY-COST EXTRACTION
                                cost = c_total.get("totalCostPrice")
                                if cost is None: cost = c_total.get("totalCostBeforeTax")
                                if cost is None: cost = c_total.get("totalCost")
                                if cost is None: cost = c_total.get("cost")
                                if cost is None: cost = consignment.get("totalCostPrice")
                                if cost is None: cost = consignment.get("totalCost")
                                if cost is None: cost = consignment.get("cost")
                                
                                if cost is not None:
                                    expected_amount = float(cost)
                                else:
                                    diagnostic_log.append("Machship record found, but 'cost' nodes are missing/null.")
                                found = True
                                break
                            else:
                                diagnostic_log.append(f"Not found via {url.split('/')[-1].split('?')[0]}")
                        else:
                            diagnostic_log.append(f"HTTP {resp.status_code} via {url.split('/')[-1].split('?')[0]}")
                    except requests.exceptions.Timeout:
                        diagnostic_log.append(f"Timeout via {url.split('/')[-1].split('?')[0]}")
                    except Exception as loop_e:
                        diagnostic_log.append(f"Exception via {url.split('/')[-1].split('?')[0]}: {str(loop_e)}")

                if not found:
                    diagnostic_log.append("Failed to locate connote across all Machship search routes.")

            # Calculate Variance & Diagnostics String
            variance = billed_amount - expected_amount
            diag_string = "Clean" if not diagnostic_log else " | ".join(diagnostic_log)

            row_data = {
                "Carrier Connote": connote,
                "Billed Amount": billed_amount,
                "Expected Amount": expected_amount,
                "Variance": variance,
                "AI Variance Analysis": "Pending Analysis",
                "Diagnostics": diag_string
            }
            reconciliation_data.append(row_data)

            # Queue items with a variance > 10 cents for Batch AI Analysis
            if found and abs(variance) > 0.10:
                analysis_batch.append({
                    "connote": connote,
                    "variance": variance,
                    "carrier_invoice_line": raw_invoice_line,
                    "machship_metrics": ms_metrics
                })

        # --- BATCH AI ANALYSIS FOR VARIANCES ---
        ai_reasons = {}
        if len(analysis_batch) > 0:
            batch_prompt = f"""
            You are a forensic freight auditor. I am providing a JSON array of consignments that have a cost variance between the Carrier Invoice and the internal WMS (Machship).
            
            For each consignment, perform a forensic natural language investigation comparing the 'carrier_invoice_line' text against the granular 'machship_metrics'. Look explicitly for:
            1. Discrepancies in Charge Weight or Cubic Volume (Did the carrier re-weigh the freight?).
            2. Missing or Added Surcharges (Did the carrier add a specific fee like 'Residential', 'Tailgate', or 'Manual Handling' that is not listed in the machship_surcharge_names?).
            3. Base rate mismatches.
            
            To help you parse the carrier line, here are the original CSV headers: {invoice_header_sample}
            
            Return ONLY a valid JSON array of objects with these keys:
            - "connote": The connote number.
            - "variance_reason": A detailed, natural language explanation of exactly why the variance occurred based on your comparison. Be definitive.
            
            Variance Data:
            {json.dumps(analysis_batch, indent=2)}
            """
            
            try:
                analysis_resp = model.generate_content(
                    batch_prompt,
                    generation_config=genai.GenerationConfig(response_mime_type="application/json")
                )
                analysis_text = analysis_resp.text.strip()
                
                amatch = re.search(r"
