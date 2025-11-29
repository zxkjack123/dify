'use client'

class StorageMock {
  data: Record<string, string>

  constructor() {
    this.data = {} as Record<string, string>
  }

  setItem(name: string, value: string) {
    this.data[name] = value
  }

  getItem(name: string) {
    return this.data[name] || null
  }

  removeItem(name: string) {
    delete this.data[name]
  }

  clear() {
    this.data = {}
  }
}

let localStorageRef: Storage | StorageMock
let sessionStorageRef: Storage | StorageMock
let shouldPatchStorage = false

try {
  localStorageRef = globalThis.localStorage
  sessionStorageRef = globalThis.sessionStorage
}
catch {
  localStorageRef = new StorageMock()
  sessionStorageRef = new StorageMock()
  shouldPatchStorage = true
}

if (shouldPatchStorage) {
  try {
    Object.defineProperty(globalThis, 'localStorage', {
      value: localStorageRef,
      configurable: true,
      writable: true,
    })
  }
  catch {
    // Some environments treat localStorage as read-only; last-ditch assignment keeps the mock reachable.
    ; (globalThis as unknown as Record<string, unknown>).localStorage = localStorageRef
  }

  try {
    Object.defineProperty(globalThis, 'sessionStorage', {
      value: sessionStorageRef,
      configurable: true,
      writable: true,
    })
  }
  catch {
    ; (globalThis as unknown as Record<string, unknown>).sessionStorage = sessionStorageRef
  }
}

const BrowserInitializer = ({
  children,
}: { children: React.ReactElement }) => {
  return children
}

export default BrowserInitializer
