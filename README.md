# A minimal time conversion discord bot

(+ some fun stuff for me to play with)

## Development

**This project uses poetry. To get started:**
1. Install poetry
2. Install the project dependencies with `poetry install`
3. Set up a MYSQL instance or get a connection url for an existing one
4. Set up the environment variables in a `.env` in the project root. All of these are only accessed in `ionic/cfg.py` for reference and an .env.example exists for reference for a development environment. The currently required environment variables are:
        
        APP_URL
        MYSQL_URL
        DISCORD_TOKEN
        HTTPS_ENABLED(=False on local)
        PIZZA_SERVER_LIST(=920027638179966996 for the test server)
        PORT(=8080)
        PYTHONUNBUFFERED=1 (optional but useful for development)

5. To run the bot run `poetry run honcho start`.
    This will start the bot + the web server used to register users.


**To run this bot in docker, set up docker and simply run:**
```
docker build -t ionic . && docker run --env-file=.env ionic
```

## Contributing

This project uses the black formatter, please do not contribute code that is not black
formatted.
