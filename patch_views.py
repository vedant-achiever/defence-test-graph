import os
import sys

filepath = 'backend/ai_engine/views.py'
with open(filepath, 'r') as f:
    content = f.read()

# Replace averaged_data with actual_data
new_func = """@csrf_exempt
@require_http_methods(["GET"])
def actual_data(request):
    \"\"\"Return actual data with sort order control.\"\"\"
    import csv
    try:
        master_csv_path = os.path.join("backend", "uploads", "master_combined.csv")
        
        if not os.path.exists(master_csv_path):
            return JsonResponse({"error": "No data."}, status=400)
        
        sort_order = request.GET.get('sort', 'asc')
        
        # Read actual data
        actual_preview = []
        with open(master_csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            actual_preview = list(reader)
            
        if sort_order == 'desc':
            actual_preview.reverse()
            
        return JsonResponse({
            "actualData": actual_preview,
            "actualCount": len(actual_preview),
            "sortOrder": sort_order,
        })
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
"""

if "def actual_data" not in content:
    content = content.replace("@csrf_exempt\n@require_http_methods([\"GET\"])\ndef averaged_data(request):", new_func + "\n@csrf_exempt\n@require_http_methods([\"GET\"])\ndef averaged_data(request):")

with open(filepath, 'w') as f:
    f.write(content)
print("patched views.py")
