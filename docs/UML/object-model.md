## enum
### users
enum UserRole {
  USER
  ADMIN
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
abstract class User {
  - id: UUID
  - email: string
  - passwordHash: string
  - role: UserRole
  - balance: CreditBalance
  - createdAt: DateTime

  + getBalance(): decimal
  + canAfford(amount: decimal): boolean
  + addCredits(amount: decimal): void
  + chargeCredits(amount: decimal): void
}

class ClientUser extends User {
  - requestHistory: MLRequestHistory

  + submitTask(model: MLModel, inputData: List<FindingRecord>): MLTask
  + requestTopUp(amount: decimal): TopUpTransaction
  + getRequestHistory(): MLRequestHistory
}

class AdminUser extends User {
  + approveTopUp(transaction: TopUpTransaction): void
  + rejectTopUp(transaction: TopUpTransaction, reason: string): void
  + viewUserTransactions(userId: UUID): List<Transaction>
}

### balance & transactions
class CreditBalance {
  - amount: decimal

  + getAmount(): decimal
  + increase(amount: decimal): void
  + decrease(amount: decimal): void
  + hasEnough(amount: decimal): boolean
}

abstract class Transaction {
  - id: UUID
  - type: TransactionType
  - amount: decimal
  - status: TransactionStatus
  - user: User
  - relatedTask: MLTask?
  - createdAt: DateTime

  + applyTo(user: User): void
  + cancel(reason: string): void
}

class TopUpTransaction extends Transaction {
  - reviewedBy: AdminUser?
  - reviewedAt: DateTime?
  - reviewComment: string?

  + approve(admin: AdminUser): void
  + reject(admin: AdminUser, reason: string): void
  + applyTo(user: User): void
}

class PredictionChargeTransaction extends Transaction {
  + applyTo(user: User): void
}

### ml
abstract class MLModel {
  - id: UUID
  - name: string
  - version: string
  - description: string
  - costPerPrediction: decimal
  - isActive: boolean

  + getCostPerPrediction(): decimal
  + predict(records: List<FindingRecord>): PredictionResult
}

class PriorityClassificationModel extends MLModel {
  + predict(records: List<FindingRecord>): PredictionResult
}

class FindingRecord {
  - recordId: string
  - payload: Map<string, string | number | boolean>

  + getRecordId(): string
  + getValue(fieldName: string): string
}

class MLTask {
  - id: UUID
  - inputData: List<FindingRecord>
  - status: MLTaskStatus
  - user: ClientUser
  - model: MLModel
  - validationErrors: List<ValidationError>
  - result: PredictionResult?
  - failureReason: string?
  - createdAt: DateTime
  - startedAt: DateTime?
  - finishedAt: DateTime?

  + start(): void
  + addValidationError(error: ValidationError): void
  + complete(result: PredictionResult): void
  + fail(reason: string): void
  + isBillable(): boolean
}

class ValidationError {
  - recordId: string
  - fieldName: string
  - message: string
  - rejectedValue: string

  + toMessage(): string
}

class PredictionResult {
  - id: UUID
  - taskId: UUID
  - predictions: List<PredictionItem>
  - processedCount: integer
  - rejectedCount: integer
  - spentCredits: decimal
  - createdAt: DateTime

  + calculateSpentCredits(costPerPrediction: decimal): decimal
  + hasSuccessfulPredictions(): boolean
}

class PredictionItem {
  - recordId: string
  - predictedPriority: PriorityClass
  - confidence: float

  + getPredictedPriority(): PriorityClass
}

### history
class MLRequestHistory {
  - owner: ClientUser
  - entries: List<RequestHistoryEntry>

  + append(entry: RequestHistoryEntry): void
  + getEntries(): List<RequestHistoryEntry>
  + getSuccessfulEntries(): List<RequestHistoryEntry>
}

class RequestHistoryEntry {
  - task: MLTask
  - result: PredictionResult?
  - chargeTransaction: PredictionChargeTransaction?
  - createdAt: DateTime

  + isSuccessful(): boolean
}

---