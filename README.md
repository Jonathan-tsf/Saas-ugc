# Configuration AWS Lambda - UGC Studio Booking

## 1. Configuration DynamoDB

La table `demos` doit avoir cette structure :
- **Partition Key (pk):** String
- **Sort Key (sk):** String

### Format des données

**Réservations:**
```
pk: BOOKINGS#2025-12
sk: 2025-12-10#14:00
```

**Paramètres de disponibilité:**
```
pk: SETTINGS#2025-12
sk: AVAILABILITY
```

## 2. Configuration Lambda

### Runtime
- Python 3.14

### Handler
- `lambda_function.lambda_handler`

### Variables d'environnement (optionnel)
Aucune requise - tout est dans le code

### Permissions IAM
La Lambda a besoin des permissions suivantes :
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "dynamodb:GetItem",
                "dynamodb:PutItem",
                "dynamodb:DeleteItem",
                "dynamodb:Query",
                "dynamodb:Scan"
            ],
            "Resource": "arn:aws:dynamodb:us-east-1:*:table/demos"
        },
        {
            "Effect": "Allow",
            "Action": [
                "ses:SendEmail",
                "ses:SendRawEmail"
            ],
            "Resource": "*"
        }
    ]
}
```

## 3. Configuration API Gateway

### Routes à créer

| Méthode | Path | Description |
|---------|------|-------------|
| GET | /api/availability | Récupère les dispos d'un mois |
| POST | /api/book-demo | Crée une réservation |
| POST | /api/admin/login | Authentification admin |
| GET | /api/admin/bookings | Liste les réservations (admin) |
| DELETE | /api/admin/bookings/{date}/{time} | Supprime une réservation |
| GET | /api/admin/settings | Récupère les paramètres |
| PUT | /api/admin/settings | Met à jour les paramètres |
| OPTIONS | /{proxy+} | CORS preflight |

### CORS Configuration
```
Access-Control-Allow-Origin: *
Access-Control-Allow-Headers: Content-Type,Authorization
Access-Control-Allow-Methods: GET,POST,PUT,DELETE,OPTIONS
```

## 4. Configuration SES

1. Vérifier l'email `jonat.tapiero@gmail.com` dans SES
2. Si en sandbox, vérifier aussi les emails des destinataires
3. Pour la production, demander à sortir du sandbox

## 5. Déploiement

### Méthode simple (Console AWS)
1. Copier le contenu de `lambda_function.py`
2. Le coller dans l'éditeur Lambda
3. Cliquer sur "Deploy"

### Méthode CLI
```bash
cd lambda
zip function.zip lambda_function.py
aws lambda update-function-code --function-name ugc-booking --zip-file fileb://function.zip
```

## 6. Test

### Test disponibilité
```bash
curl "https://och8urskml.execute-api.us-east-1.amazonaws.com/production/api/availability?month=2025-12"
```

### Test réservation
```bash
curl -X POST "https://och8urskml.execute-api.us-east-1.amazonaws.com/production/api/book-demo" \
  -H "Content-Type: application/json" \
  -d '{"name":"Test","email":"test@test.com","start_time":"2025-12-10T14:00:00"}'
```

### Test admin login
```bash
curl -X POST "https://och8urskml.execute-api.us-east-1.amazonaws.com/production/api/admin/login" \
  -H "Content-Type: application/json" \
  -d '{"password":"JT14032001!"}'
```

## URLs

- **API:** https://och8urskml.execute-api.us-east-1.amazonaws.com/production
- **Site:** http://localhost:3000/fr/booking
- **Admin:** http://localhost:3000/fr/admin

## Mot de passe Admin
`JT14032001!`
