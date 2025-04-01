
# mcp-feishu-bot

一个简单的mcp结合飞书机器人的项目



部署步骤：

```
git clone https://github.com/twwch/mcp-feishu-bot.git
cd mcp-feishu-bot
copy src/.env.example to src/.env
```

在src/.env中修改配置， 目前仅支持AZURE_OPENAI_MODEL 和 OPENAI_MODEL

创建虚拟环境python环境
```
conda create -n mcp-feishu-bot python=3.12 -y

conda activate mcp-feishu-bot

pip install -r requirements.txt
```


安装完成后运行
```
cd src
python app.py
```
