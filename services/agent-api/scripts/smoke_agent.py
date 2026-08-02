import asyncio

from agent_api.agent import create_agent, create_ollama_http_client


async def main() -> None:
    # The explicit client context guarantees the connection pool is closed.
    async with create_ollama_http_client() as http_client:
        agent = create_agent(http_client)

        async with agent:
            result = await agent.run("请用一句中文说明你已成功连接到 AgentOS。")

    print(result.output)


if __name__ == "__main__":
    asyncio.run(main())
