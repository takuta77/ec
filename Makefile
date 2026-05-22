.PHONY: load-jwt

load-jwt:
	@printf 'JWT_PRIVATE_KEY="%s"\nJWT_PUBLIC_KEY="%s"\n' \
		"$$(cat secrets/jwt_private.pem)" \
		"$$(cat secrets/jwt_public.pem)" > .env.jwt
	@echo "Wrote .env.jwt. Use: docker-compose --env-file .env --env-file .env.jwt up"
