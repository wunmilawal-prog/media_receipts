# import os
# import requests
# from dotenv import load_dotenv
# import json

# # login_url = "https://api-platform.functionpoint.com/login"

# # # login_data = {
# # #     "company": os.environ["FP_COMPANY"],
# # #     "username": os.environ["FP_USERNAME"],
# # #     "password": os.environ["FP_PASSWORD"],
# # # }


# # response = requests.post(login_url, json=login_data, timeout=30)
# # print("Status:", response.status_code)
# # print("Response:", response.text)

# load_dotenv()
# api_key = os.environ["FP_API_KEY"]

# headers = {
#     "Authorization": f"Bearer {api_key}",
#     "Accept": "application/json",
# }

# job_number = 3265

# response = requests.get(
#     "https://api-platform.functionpoint.com/dockets",
#     headers=headers,
#     params ={"number": job_number},
#     timeout=30,
# )
# response.raise_for_status()

# matches = response.json()
# if not matches:
#     raise RuntimeError(f"No Function Point job found for number {job_number}")

# if len(matches) > 1:
#     raise RuntimeError(
#         f"Multiple Function Point jobs found for number {job_number}"
#     )
# docket_id = matches[0]["docketid"]

# # print("Job number:", job_number)
# # print("Docket ID:", docket_id)
# # print("Job name:", matches[0].get("name"))

# #second request: retrieve complete job
# detail_response = requests.get(
#     f"https://api-platform.functionpoint.com/dockets/{docket_id}",
#     headers=headers,
#     timeout=30,
# )
# detail_response.raise_for_status()

# data = detail_response.json()

# #print(json.dumps(data, indent=2))



# for estimate in data.get("estimates", []):
#     for phase in estimate.get("estimatePhases", []):
#         print("\nService Group:", phase.get("name"))

#         for service in phase.get("estimateServices", []):
#             expense = service.get("externalexpense")

#             if expense:
#                 print("Expense Type:", expense.get("name"))
#                 print("Expense Type ID:", expense.get("externalexpenseid"))
#                 print("Expense Type Code:", expense.get("code"))

import os
import requests
from dotenv import load_dotenv

import process_media_receipts as processor

load_dotenv()

filename = "Cineplex - DirectEnergy CFMWS-3480.pdf"
pdf_text = ""  # Filename-only test

# Parse the filename
fp_codes = processor.load_supplier_codes()

fp_code, supplier, tax_group, supplier_confidence = (
    processor.detect_supplier(filename, pdf_text, fp_codes)
)

invoice_number = processor.extract_invoice_number(filename, pdf_text)
job_codes, job_source = processor.extract_job_codes(filename, pdf_text)

print("Parsed filename")
print("Supplier:", supplier)
print("Supplier code:", fp_code)
print("Invoice number:", invoice_number)
print("Job codes:", job_codes)

if len(job_codes) != 1:
    raise RuntimeError("This preview requires exactly one job")

job_number = int(job_codes[0].split("-")[-1])

# Connect to Function Point
api_key = os.environ["FP_API_KEY"]

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {api_key}",
    "Accept": "application/json",
})

job = processor.get_function_point_job(
    job_number,
    session,
    cache={},
)

match = processor.match_supplier_to_function_point_expense(
    job,
    fp_code,
    supplier,
)

description = processor.build_description(
    supplier,
    invoice_number,
    job_codes,
)

print("\nFunction Point result")
print("Docket ID:", job.get("docketid"))
print("Job name:", job.get("name"))
print("Service Group:", match["service_group"])
print("Expense Type:", match["expense_type"])
print("Expense Type ID:", match["expense_type_id"])
print("Expense Type Code:", match["expense_type_code"])

print("\nCSV preview")
preview = {
    "Reference Number": invoice_number,
    "*Supplier": supplier,
    "Description": description,
    "*Job": job_number,
    "*Expense Type": match["expense_type"],
    "Tax Group": tax_group,
    "Service Group": match["service_group"],
}

for column, value in preview.items():
    print(f"{column}: {value}")