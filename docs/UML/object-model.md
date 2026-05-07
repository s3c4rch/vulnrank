## enum
### users
enum UserRole {
  USER
  ADMIN
}

enum ExternalProvider {
  OPENAI
}

### ml
enum MLTaskStatus {
  CREATED
  VALIDATING
  PROCESSING
  COMPLETED
  FAILED
}

enum PriorityClass {
  LOW
  MEDIUM
  HIGH
}

### balance & transactions
enum TransactionType {
  TOP_UP
  PREDICTION_CHARGE
}

enum TransactionStatus {
  PENDING
  APPROVED
  REJECTED
  COMPLETED
}

---

## classes & methods
### users
class User {
  - id: UUID
  - email: string
  - passwordHash: string
  - role: UserRole
  - createdAt: DateTime

  + getBalance(): Balance
  + getTasks(): List<MLTask>
  + getTransactions(): List<Transaction>
}

class AuthSession {
  - id: UUID
  - userId: UUID
  - token: string
  - createdAt: DateTime

  + isActive(): boolean
}

class UserExternalModelCredential {
  - id: UUID
  - userId: UUID
  - provider: ExternalProvider
  - apiKey: string
  - modelName: string
  - isEnabled: boolean
  - createdAt: DateTime
  - updatedAt: DateTime

  + enable(): void
  + disable(): void
  + updateModel(modelName: string): void
}

### ml
class MLModel {
  - id: UUID
  - name: string
  - version: string
  - description: string
  - costPerPrediction: decimal
  - isActive: boolean
  - createdAt: DateTime

  + activate(): void
  + deactivate(): void
  + getCostPerPrediction(): decimal
}

class MLTask {
  - id: UUID
  - userId: UUID
  - modelId: UUID
  - status: MLTaskStatus
  - inputPayload: JSON
  - spentCredits: decimal
  - errorMessage: string?
  - createdAt: DateTime
  - finishedAt: DateTime?

  + startValidation(): void
  + startProcessing(): void
  + complete(): void
  + fail(reason: string): void
  + hasResult(): boolean
}

class PredictionResult {
  - id: UUID
  - taskId: UUID
  - predictedPriority: PriorityClass
  - predictionValue: float?
  - confidence: float
  - processedCount: integer
  - rejectedCount: integer
  - spentCredits: decimal
  - workerId: string?
  - createdAt: DateTime

  + hasSuccessfulPredictions(): boolean
  + getProcessedCount(): integer
  + getRejectedCount(): integer
}

### balance & transactions
class Balance {
  - id: UUID
  - userId: UUID
  - amount: decimal
  - updatedAt: DateTime

  + canAfford(amount: decimal): boolean
  + increase(amount: decimal): void
  + decrease(amount: decimal): void
}

class Transaction {
  - id: UUID
  - userId: UUID
  - taskId: UUID?
  - type: TransactionType
  - status: TransactionStatus
  - amount: decimal
  - reviewComment: string?
  - createdAt: DateTime

  + approve(): void
  + reject(reason: string): void
  + complete(): void
}

---
