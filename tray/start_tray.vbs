' pim 托盘客户端启动器 —— 双击运行, 无控制台窗口
' 注意: 用 python(带控制台) 启动并隐藏窗口, 而非 pythonw ——
' 部分机器上代理软件/安全软件会拦杀 pythonw 发起的网络连接
' (PermissionError 13), python.exe 进程形态实测不受影响。
' 若 python 不在 PATH 中, 请把下面 RUN 的 "python" 改为完整路径
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run "python """ & dir & "\pim_tray.py""", 0, False
