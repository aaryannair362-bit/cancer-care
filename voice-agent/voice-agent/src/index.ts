import express from 'express'
import dotenv from 'dotenv'

dotenv.config()

const app = express()
const PORT = process.env.PORT || 3001

app.use(express.json())

app.get('/health', (req, res) => {
  res.status(200).json({
    status: 'ok',
    timestamp: new Date().toISOString(),
    service: 'healthcare-voice-agent',
  })
})

app.listen(PORT, () => {
  console.log(`Voice agent service running on port ${PORT}`)
})
