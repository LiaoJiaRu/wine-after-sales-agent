import sys
from app.config import settings
from langchain_openai import ChatOpenAI
from langfuse import Langfuse


def check_all():
    print("=" * 50)
    print("1. 检查环境变量加载")
    print("=" * 50)
    if not settings.deepseek_api_key:
        print("❌ DEEPSEEK_API_KEY 未设置或为空")
    else:
        print(f"✅ DEEPSEEK_API_KEY 已加载 (前缀: {settings.deepseek_api_key[:5]}...)")
    print(f"✅ DEEPSEEK_BASE_URL: {settings.deepseek_base_url}")

    if settings.langfuse_enabled:
        print(f"✅ LANGFUSE_ENABLED: True")
        print(f"✅ LANGFUSE_HOST: {settings.langfuse_host}")
        if not settings.langfuse_public_key or not settings.langfuse_secret_key:
            print("❌ LANGFUSE 的 KEY 未填写完整")
        else:
            print(f"✅ LANGFUSE 的 KEY 已加载")
    else:
        print("⚠️ LANGFUSE_ENABLED 为 False，跳过 Langfuse 测试")

    print("\n" + "=" * 50)
    print("2. 测试 DeepSeek 大模型连通性")
    print("=" * 50)
    if not settings.deepseek_api_key:
        print("⚠️ 无 Key，跳过测试")
    else:
        try:
            print("正在请求 DeepSeek (可能耗时 2-5 秒)...")
            llm = ChatOpenAI(
                model="deepseek-chat",
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                timeout=10
            )
            res = llm.invoke("请只回复四个字：连接成功")
            print(f"✅ 大模型回复: {res.content}")
        except Exception as e:
            print(f"❌ DeepSeek 连接失败，报错信息:\n{e}")

    # 3. 测试 Langfuse 连通性 (T6)
    print("\n" + "=" * 50)
    print("3. 测试 Langfuse 连通性 (T6)")
    print("=" * 50)

    if settings.langfuse_enabled and settings.langfuse_public_key:
        try:
            from langfuse import Langfuse
            langfuse = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key,
                host=settings.langfuse_host
            )

            # 1. 鉴权
            auth_res = langfuse.auth_check()
            if not auth_res:
                print("❌ Langfuse 鉴权失败，请检查 Public/Secret Key 是否匹配。")
                return
            print("✅ Langfuse 鉴权成功！")

            # 2. 用新版 SDK API 创建 Trace
            print("正在发送测试 Trace...")
            with langfuse.start_as_current_observation(
                    as_type="span",
                    name="env_check_test"  # 你可以在 Langfuse 界面看到这个名字
            ) as span:
                span.update(input="hello from check_env", output="world")

            # 3. 刷新缓冲区，确保数据发送
            langfuse.flush()
            print("✅ 测试 Trace 已上报！请去 Langfuse 网页端查看。")

        except Exception as e:
            print(f"❌ Langfuse 连接失败，报错信息:\n{e}")
    else:
        print("⚠️ Langfuse 未开启或未配置，跳过测试")


if __name__ == "__main__":
    check_all()