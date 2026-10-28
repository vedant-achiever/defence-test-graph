import re

with open('index.html', 'r') as f:
    content = f.read()

# 1. Remove attributes, combination, export tabs
content = re.sub(r"\s*\{\s*id:\s*'attributes',\s*label:\s*'Attributes',\s*icon:\s*'file-spreadsheet'\s*\},", "", content)
content = re.sub(r"\s*\{\s*id:\s*'combination',\s*label:\s*'Combination',\s*icon:\s*'merge'\s*\},", "", content)
content = re.sub(r"\s*\{\s*id:\s*'export',\s*label:\s*'Export',\s*icon:\s*'download'\s*\},", "", content)

# 2. Change averagedData to actualData in state
content = content.replace("averagedData", "actualData")

# 3. Change "Combine data first" to "Upload data first"
content = content.replace("Combine data first to", "Upload data first to")
content = content.replace("Combine data first", "Upload data first")

# 4. In Analysis view, rename Minute-Averaged Analysis to Actual Data Analysis
content = content.replace("Minute-Averaged Analysis", "Actual Data Analysis")

# 5. changeSortOrder function
# from `/api/averaged-data?sort=' + order` to `/api/actual-data?sort=' + order`
content = content.replace("/api/averaged-data", "/api/actual-data")

# 6. We also need to automatically fetch actualData after upload
# Find handleFileUpload and add the fetch to actual-data
old_upload = """                    if (result.files && result.files.length > 0) {
                        state.files = result.files;
                    } else {
                        state.files = new Array(result.fileCount).fill({ filename: "Uploaded File (Backend)", rowCount: "-", columnCount: "-" });
                    }
                    state.attributes = result.attributes || [];
                }"""
new_upload = """                    if (result.files && result.files.length > 0) {
                        state.files = result.files;
                    } else {
                        state.files = new Array(result.fileCount).fill({ filename: "Uploaded File (Backend)", rowCount: "-", columnCount: "-" });
                    }
                    state.attributes = result.attributes || [];
                    
                    // Fetch actual data
                    const actualRes = await fetch('/api/actual-data');
                    if (actualRes.ok) {
                        const actualResult = await actualRes.json();
                        state.actualData = actualResult.actualData || [];
                        state.totalCombinedCount = actualResult.actualCount || 0;
                        state.combinedData = state.actualData; // For filters and other tabs
                    }
                }"""
content = content.replace(old_upload, new_upload)

with open('index.html', 'w') as f:
    f.write(content)

print("patched index.html")
