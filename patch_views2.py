import os

filepath = 'backend/ai_engine/views.py'
with open(filepath, 'r') as f:
    content = f.read()

# Make upload_telemetry copy the first file to master_combined.csv
old_code = """                workspace.add_uploaded_file(f.name, file_path)
                parsed_count += 1"""
new_code = """                workspace.add_uploaded_file(f.name, file_path)
                parsed_count += 1
                
                master_csv_path = os.path.join(upload_dir, "master_combined.csv")
                import shutil
                shutil.copy2(file_path, master_csv_path)"""
                
if "master_combined.csv" not in content.split("def upload_telemetry")[1].split("def ")[0]:
    content = content.replace(old_code, new_code)

with open(filepath, 'w') as f:
    f.write(content)
print("patched views.py upload")
