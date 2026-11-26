' pim 托盘客户端启动器 —— 双击运行, 无控制台窗口
' 依赖 pythonw 在 PATH 中; 若不在, 请把下面 RUN 的 "pythonw" 改为完整路径
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run "pythonw """ & dir & "\pim_tray.py""", 0, False
