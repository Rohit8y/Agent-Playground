from __future__ import annotations as _annotations

from dotenv import load_dotenv

import asyncio
import os
from dataclasses import dataclass
from typing import Any, List
from httpx import AsyncClient

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.gemini import GeminiModel
from pydantic import BaseModel

load_dotenv()  # take environment variables


class Structured_Output(BaseModel):
    location: str
    temperature: str
    aqi: int
    description: str


@dataclass
class Deps:
    client: AsyncClient
    weather_api_key: str | None
    geo_api_key: str | None
    aqi_api_key: str | None


model = GeminiModel('gemini-2.0-flash', provider='google-gla')
weather_agent = Agent(
    model=model,
    # 'Be concise, reply with one sentence.' is enough for some models (like openai) to use
    # the below tools appropriately, but others like anthropic and gemini require a bit more direction.
    system_prompt=(
        'Be concise, reply with one sentence.'
        'Use the `get_lat_lng` tool to get the latitude and longitude of the locations, '
        'Use the `get_aqi` tool to get the aqi of each latitude and longitude locations, '
        'then use the `get_weather` tool to get the weather.'
        'If the query is not related to weather, deny the user politely'
    ),
    deps_type=Deps,
    retries=2,
    instrument=True,
    output_type=List[Structured_Output],
)

import requests
from pydantic import BaseModel
from typing import Optional


@weather_agent.tool
def get_aqi(ctx: "RunContext[Deps]", lat: float, lon: float) -> Optional[int]:
    url = f"http://api.waqi.info/feed/geo:{lat};{lon}/?token={ctx.deps.aqi_api_key}"

    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("status") == "ok" and "data" in data and "aqi" in data["data"]:
            return data["data"]["aqi"]
        else:
            return None

    except requests.RequestException as e:
        print(f"Request failed: {e}")
        return None


@weather_agent.tool
async def get_lat_lng(
        ctx: RunContext[Deps], location_description: str
) -> dict[str, float]:
    """Get the latitude and longitude of a location.

    Args:
        ctx: The context.
        location_description: A description of a location.
    """
    if ctx.deps.geo_api_key is None:
        # if no API key is provided, return a dummy response (London)
        return {'lat': 51.1, 'lng': -0.1}

    params = {
        'q': location_description,
        'api_key': ctx.deps.geo_api_key,
    }
    r = await ctx.deps.client.get('https://geocode.maps.co/search', params=params)
    r.raise_for_status()
    data = r.json()

    if data:
        return {'lat': data[0]['lat'], 'lng': data[0]['lon']}
    else:
        raise ModelRetry('Could not find the location')


@weather_agent.tool
async def get_weather(ctx: RunContext[Deps], lat: float, lng: float) -> dict[str, Any]:
    """Get the weather at a location.

    Args:
        ctx: The context.
        lat: Latitude of the location.
        lng: Longitude of the location.
    """
    if ctx.deps.weather_api_key is None:
        # if no API key is provided, return a dummy response
        return {'temperature': '21 °C', 'description': 'Sunny'}

    params = {
        'apikey': ctx.deps.weather_api_key,
        'location': f'{lat},{lng}',
        'units': 'metric',
    }
    r = await ctx.deps.client.get(
        'https://api.tomorrow.io/v4/weather/realtime', params=params
    )
    r.raise_for_status()
    data = r.json()

    values = data['data']['values']
    # https://docs.tomorrow.io/reference/data-layers-weather-codes
    code_lookup = {
        1000: 'Clear, Sunny',
        1100: 'Mostly Clear',
        1101: 'Partly Cloudy',
        1102: 'Mostly Cloudy',
        1001: 'Cloudy',
        2000: 'Fog',
        2100: 'Light Fog',
        4000: 'Drizzle',
        4001: 'Rain',
        4200: 'Light Rain',
        4201: 'Heavy Rain',
        5000: 'Snow',
        5001: 'Flurries',
        5100: 'Light Snow',
        5101: 'Heavy Snow',
        6000: 'Freezing Drizzle',
        6001: 'Freezing Rain',
        6200: 'Light Freezing Rain',
        6201: 'Heavy Freezing Rain',
        7000: 'Ice Pellets',
        7101: 'Heavy Ice Pellets',
        7102: 'Light Ice Pellets',
        8000: 'Thunderstorm',
    }
    return {
        'temperature': f'{values["temperatureApparent"]:0.0f}°C',
        'description': code_lookup.get(values['weatherCode'], 'Unknown'),
    }


async def main():
    async with AsyncClient() as client:
        # create a free API key at https://www.tomorrow.io/weather-api/
        weather_api_key = os.getenv('WEATHER_API_KEY')
        # create a free API key at https://geocode.maps.co/
        geo_api_key = os.getenv('GEO_API_KEY')
        # AQI KEY
        aqi_api_key = os.getenv('AQI_API_KEY')

        deps = Deps(
            client=client, weather_api_key=weather_api_key, geo_api_key=geo_api_key, aqi_api_key=aqi_api_key
        )
        result = await weather_agent.run(
            'What is the weather in Amsterdam?', deps=deps
        )
        # debug(result)
        print('Response:', result.output)


if __name__ == '__main__':
    asyncio.run(main())
