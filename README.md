# 用别怕，怕别用
AuthLeak By Ver.CN 1.53
## 使用须知
1.使用本工具造成的任何法律纠纷、账号封禁、数据丢失等后果，由**使用者本人**承担，开发者不承担任何责任，**严禁用于任何商业或非法用途**。</br>
2.本项目基于**Python**语言编写，使用前请确保您的Python环境**完好无缺**</br>
3.本项目使用**uv**这个好用的虚拟环境。</br>
### 部署教程
首先将仓库Clone下来
```powershell
# Clone仓库
git clone https://github.com/Te-River/AuthLeak.git
```
之后就可以进行部署了
```powershell
# 进入Clone的目录
cd .\AuthLeak\

# 安装uv库
pip install uv

# 根据pyproject.toml文件同步依赖
uv sync

# 运行
uv run python AuthLeak.py
```
