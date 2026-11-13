# 宿主与服务端文件交接

先确认文件实际在哪一台机器。工具只有MCP接口，不能通过一个客户端路径远程读文件。宿主无本地文件工具时请用户或管理员提供上传后的服务端位置，不伪造上传完成。

## 上传

1. `openmc_file_limits`查询当前限额与分块限制；默认完整文件5 MiB，可由管理员配置，ZIP展开也有总量限额。超限先选必要输入或请管理员调整，不能拆包绕过完整文件限制。核数据通常留服务器部署。
2. 宿主客户端读取实际字节并计算完整SHA256，通过 `openmc_upload_begin`建立会话；保存transfer_id与请求回执。
3. 根据当前工具Schema用 `openmc_upload_chunk`分块；中断后先 `openmc_upload_status`核对已有offset/前缀摘要，再续传。`openmc_upload_finish`校验完整摘要并发布。使用返回的实际发布目录调用导入工具，不猜inbox路径。
4. ZIP只打包纯模型输入及相对依赖，不带运行时、核数据库、旧作业或生成的views。拒绝/未完成时保留原文件，不将暂存目录视为可提交模型。

## 预览与下载

1. 通过 `openmc_list_artifacts`选择job/scan的已登记产物；已上传文件也可作为`upload`类型选择。不是任意服务器目录下载接口。
2. 小文本/图像用 `openmc_preview_file`；完整报告或选定产物用 `openmc_prepare_download`取得下载会话，再 `openmc_download_chunk`。恢复先查 `openmc_download_status`，按offset续传。
3. 宿主将字节写入本地文件并核对完整SHA256，确认实际存在后才能给用户本地路径。`openmc_close_transfer`关闭不再需要的暂存；不要删除科学任务原始证据。

## 已提供的客户端

工具包 `examples/transfer_files.py`直接处理字节，不经过语言模型。它应在拥有源/目标文件的宿主机器运行，需要Python及MCP客户端依赖；命令行用其`--help`查询。它会核对服务版本与文件工具定义，地址通过`--mcp-url`提供，鉴权用`MCP_AUTH_TOKEN`环境变量，不在聊天中索取密钥。

只有服务端包时，不要假设宿主机器也有此脚本；需要将随包客户端及其依赖放到宿主，或使用宿主等效本地文件能力。完整安装与示例在工具包 `docs/deployment.html#file-transfer-config`和`docs/toolkit-usage.html#file-transfer`。
