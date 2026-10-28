import os

filepath = 'backend/ai_engine/urls.py'
with open(filepath, 'r') as f:
    content = f.read()

if "actual_data" not in content:
    content = content.replace("views.averaged_data, name='averaged_data'),", "views.averaged_data, name='averaged_data'),\n    path('api/actual-data', views.actual_data, name='actual_data'),")

with open(filepath, 'w') as f:
    f.write(content)
print("patched urls.py")
