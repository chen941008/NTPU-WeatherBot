import sys
import os
from dotenv import load_dotenv
load_dotenv()

# 1. 設定路徑
folder_name = 'howtocook-py-mcp-master'
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(current_dir, folder_name))

print(f"📂 正在檢查檔案：{folder_name}/src/data/recipes.py")

try:
    # 2. 直接匯入整個模組 (不指定名字)
    import src.data.recipes as target_file
    
    print("\n✅ 檔案匯入成功！")
    print("👀 裡面的變數名稱有這些：")
    
    # 3. 印出所有不以底線開頭的變數名稱
    names = [n for n in dir(target_file) if not n.startswith('__')]
    print(names)
    
    print("\n" + "="*30)
    if 'RECIPES' in names:
        print("💡 找到啦！它叫做 'RECIPES' (全大寫)")
    elif 'get_recipes' in names:
         print("💡 找到啦！它可能是個函式 'get_recipes'")
    else:
        print("💡 請告訴我上面印出的清單裡，哪個看起來像『食譜列表』？")
        
except ImportError as e:
    print(f"❌ 還是匯入失敗：{e}")
except Exception as e:
    print(f"❌ 發生錯誤：{e}")