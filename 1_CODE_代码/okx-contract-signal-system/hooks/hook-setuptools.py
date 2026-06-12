from PyInstaller.utils.hooks import collect_data_files

# 强制收集 setuptools._vendor.jaraco.text 的数据文件（包含 Lorem ipsum.txt）
datas = collect_data_files('setuptools._vendor.jaraco.text')