# 宿主与服务端文件交接

先确认文件实际在哪一台机器。工具只有MCP接口，不能通过一个客户端路径远程读文件。宿主无本地文件工具时请用户或管理员提供上传后的服务端位置，不伪造上传完成。

## 上传

1. `openmc_file_limits`查询当前限额与分块限制；默认完整文件5 MiB，可由管理员配置，ZIP展开也有总量限额。超限先选必要输入或请管理员调整，不能拆包绕过完整文件限制。核数据通常留服务器部署。
2. 宿主客户端读取实际字节并计算完整SHA256，通过 `openmc_upload_begin`建立会话；保存transfer_id与请求回执。
3. 根据当前工具Schema用 `openmc_upload_chunk`分块；中断后先 `openmc_upload_status`核对已有offset/前缀摘要，再续传。`openmc_upload_finish`校验完整摘要并发布。使用返回的实际发布目录调用导入工具，不猜inbox路径。
4. ZIP只打包纯模型输入及相对依赖，不带运行时、核数据库、旧作业或生成的views。拒绝/未完成时保留原文件，不将暂存目录视为可提交模型。

## 预览与下载

1. 已有结果包时直接使用其返回的实际文件path；不必为同份产物再列清单。尚不知道文件时通过 `openmc_list_artifacts`选择job/scan的已登记产物；已上传文件也可作为`upload`类型选择。不是任意服务器目录下载接口。
2. 小文本/图像用 `openmc_preview_file`；完整报告或选定产物用 `openmc_prepare_download`取得下载会话，再 `openmc_download_chunk`。恢复先查 `openmc_download_status`，按offset续传。
3. 宿主将字节写入本地文件并核对完整SHA256，确认实际存在后才能给用户本地路径。客户端返回local_file_verified=true及实际file；transfer_cleanup.state=pending只说明暂存关闭未确认，本机文件仍可用，沿原回执重试清理，不重新下载或计算。`openmc_close_transfer`关闭不再需要的暂存；不要删除科学任务原始证据。

## 已提供的客户端

工具包 `examples/transfer_files.py`直接处理字节，不经过语言模型。它应在拥有源/目标文件的宿主机器运行，需要Python及MCP客户端依赖；命令行用其`--help`查询。它会核对服务版本与文件工具定义，地址通过`--mcp-url`提供，鉴权用`MCP_AUTH_TOKEN`环境变量，不在聊天中索取密钥。

只有服务端包时，不要假设宿主机器也有此脚本；需要将随包客户端及其依赖放到宿主，或使用宿主等效本地文件能力。完整安装与示例在工具包 `docs/deployment.html#file-transfer-config`和`docs/toolkit-usage.html#file-transfer`。

## Agent可直接复用的命令

在有MCP客户端依赖的宿主Python中执行，文件字节不经过模型。服务端OpenMC Python与宿主客户端Python可能是不同环境；使用前核对客户端能连接并导入mcp，不因缺依赖改模型配置或让模型逐块搬Base64。

已知结果包路径时一步准备并下载（保留paths的实际原文）：
```sh
python /工具包/examples/transfer_files.py --mcp-url <实际MCP地址> download job <job_id> '<结果包path>' '<另一个path>' --output '<本机成果.zip>'
```
若已调用openmc_prepare_download并拿到transfer_id，直接接着下载，不必再次准备：
```sh
python /工具包/examples/transfer_files.py --mcp-url <实际MCP地址> download --transfer-id <transfer_id> --output '<本机成果.zip>'
```
断线后原命令及回执恢复。普通结果包路径不必先list_artifacts；完整文件已保存时只重试清理。宿主缺少本地执行/写入能力时说明未取回，不在模型上下文手工拼字节。
