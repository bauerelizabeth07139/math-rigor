import { access, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { constants } from 'node:fs'
import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { dirname, join, resolve } from 'node:path'
import { homedir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import z from '@deepseek-ai/schemastery'
import { BUNDLED_SKILL_RANK } from '@deepseek-ai/dsh-skill'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { apply as applyMcpClient, Config as McpClientConfig, inject as mcpClientInject, name as mcpClientName } from '@deepseek-ai/dsh-mcp-client'

export const name = 'dsh-math-rigor'
export const inject = ['tools', 'skills', 'commands']
export const Config = z.object({
  home: z.string().default(''),
  python: z.string().default(''),
  setup: z.boolean().default(false),
  setupTimeoutMs: z.number().step(1).min(1000).max(600000).default(600000),
  toolCallTimeoutMs: z.number().step(1).min(1000).max(600000).default(300000),
})

const ROOT = dirname(fileURLToPath(import.meta.url))
const SERVER = join(ROOT, 'server', 'math_rigor_server.py')
const SKILL_ROOT = join(ROOT, 'skills', 'math-rigor')
const SKILL_PATH = join(SKILL_ROOT, 'SKILL.md')
const REQUIREMENTS = join(ROOT, 'requirements.txt')
const SETUP_SCRIPT = join(ROOT, 'tools', 'setup_dsh.py')

function expandHomePath(value) {
  if (value === '~') return homedir()
  if (value.startsWith('~/') || value.startsWith('~\\')) return join(homedir(), value.slice(2))
  return value
}

const configuredDshHome = process.env.DSH_HOME?.trim()
const DEFAULT_HOME = configuredDshHome
  ? resolve(expandHomePath(configuredDshHome), 'math-rigor')
  : join(homedir(), '.dsh', 'math-rigor')

async function exists(path) {
  try {
    await access(path, constants.F_OK)
    return true
  } catch {
    return false
  }
}

function run(command, args, options = {}) {
  return new Promise((resolvePromise, reject) => {
    const { timeoutMs = 0, ...spawnOptions } = options
    const child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'], ...spawnOptions })
    let stdout = ''
    let stderr = ''
    let settled = false
    let timedOut = false
    const finishReject = error => {
      if (settled) return
      settled = true
      reject(error)
    }
    const timer = timeoutMs > 0 ? setTimeout(() => {
      timedOut = true
      if (process.platform === 'win32' && child.pid) {
        const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true })
        killer.once('error', () => child.kill())
      } else {
        child.kill('SIGTERM')
      }
    }, timeoutMs) : undefined
    child.stdout?.on('data', chunk => { stdout += chunk })
    child.stderr?.on('data', chunk => { stderr += chunk })
    child.once('error', error => {
      if (timer) clearTimeout(timer)
      finishReject(timedOut ? new Error(`timed out after ${timeoutMs}ms`) : error)
    })
    child.once('close', code => {
      if (timer) clearTimeout(timer)
      if (settled) return
      if (timedOut) {
        finishReject(new Error(`timed out after ${timeoutMs}ms`))
        return
      }
      settled = true
      resolvePromise({ code: code ?? 1, stdout, stderr })
    })
  })
}

async function validatePython(command, timeoutMs) {
  const launcherArgs = process.platform === 'win32' && /(?:^|[\\/])py(?:\.exe)?$/iu.test(command) ? ['-3'] : []
  const result = await run(command, [...launcherArgs, '-c', 'import sys; print(sys.executable); raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'], { timeoutMs })
  if (result.code !== 0) throw new Error('Python 3.10+ was not found or could not be executed.')
  return result.stdout.trim().split(/\r?\n/).at(-1)
}

async function choosePython(requested, remainingMs) {
  const candidates = requested ? [requested] : (process.platform === 'win32' ? ['python.exe', 'py'] : ['python3', 'python'])
  for (const candidate of candidates) {
    try { return await validatePython(candidate, remainingMs()) } catch (error) {
      if (Date.now() >= remainingMs.deadline) throw error
    }
  }
  throw new Error('Python 3.10+ was not found. Install Python and retry.')
}

async function ensureEnvironment(home, requestedPython, setupEnabled, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  const remainingMs = () => {
    const remaining = deadline - Date.now()
    if (remaining <= 0) throw new Error(`math-rigor: setup timed out after ${timeoutMs}ms`)
    return remaining
  }
  remainingMs.deadline = deadline
  const envRoot = join(home, 'venv')
  const venvPython = process.platform === 'win32' ? join(envRoot, 'Scripts', 'python.exe') : join(envRoot, 'bin', 'python')
  const marker = join(envRoot, '.requirements.sha256')
  const requirementsHash = createHash('sha256').update(await readFile(REQUIREMENTS)).digest('hex')
  await mkdir(home, { recursive: true })
  let installedHash = ''
  try { installedHash = (await readFile(marker, 'utf8')).trim() } catch { /* setup is required */ }
  let healthy = false
  if (await exists(venvPython)) {
    const probe = await run(venvPython, ['-c', 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'], { timeoutMs: remainingMs() })
    if (probe.code !== 0) {
      if (!setupEnabled) throw new Error('math-rigor: existing Python environment is older than 3.10; remove its venv and rerun tools/setup_dsh.py.')
      await rm(envRoot, { recursive: true, force: true })
    } else if (installedHash === requirementsHash) {
      const dependencyProbe = await run(venvPython, ['-c', 'import mcp, sympy, z3, mpmath'], { timeoutMs: remainingMs() })
      healthy = dependencyProbe.code === 0
    }
  }
  if (healthy) return venvPython
  if (!setupEnabled) throw new Error(`math-rigor: Python environment is not prepared or is out of date. Enable setup or run tools/setup_dsh.py with --home "${home}".`)
  if (!(await exists(venvPython))) {
    const python = await choosePython(requestedPython, remainingMs)
    const created = await run(python, ['-m', 'venv', envRoot], { cwd: ROOT, timeoutMs: remainingMs() })
    if (created.code !== 0 || !(await exists(venvPython))) throw new Error(`math-rigor: could not create its Python environment: ${created.stderr.trim()}`)
  }
  const installed = await run(venvPython, ['-m', 'pip', 'install', '--disable-pip-version-check', '-r', REQUIREMENTS], { cwd: ROOT, timeoutMs: remainingMs() })
  if (installed.code !== 0) throw new Error(`math-rigor: dependency installation failed: ${installed.stderr.trim()}`)
  await writeFile(marker, `${requirementsHash}\n`, 'utf8')
  return venvPython
}

async function readSkill() {
  const fs = await import('node:fs/promises')
  return (await fs.readFile(SKILL_PATH, 'utf8')).replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/u, '').trim()
}

async function commandMessage(command, rawInput) {
  const input = rawInput.trim()
  const template = await readFile(join(ROOT, 'commands', `${command}.md`), 'utf8')
  const workflow = template.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/u, '').trim().split('$ARGUMENTS').join(input)
  const text = [
    'Load the `math-rigor` skill before acting, then follow the workflow below.',
    'Use the actual MCP tool names shown in the tool catalog; they are prefixed with `mcp__math_rigor__` in DSH.',
    workflow,
  ].join('\n\n')
  return createUserMessage({
    content: [{ type: 'text', text }],
    source: { kind: 'user' },
  })
}

function registerCommands(ctx, isReady, home) {
  const setupPython = process.platform === 'win32' ? 'py -3' : 'python3'
  const notReady = () => ({ kind: 'error', text: `math-rigor MCP tools are not ready. Run ${setupPython} "${SETUP_SCRIPT}" --home "${home}" and restart the DSH profile.` })
  ctx.commands.register({
    name: 'prove',
    description: 'run a rigorous, machine-checkable mathematical proof workflow',
    input: { hint: '<mathematical proposition>' },
    handler: async invocation => {
      if (!isReady()) return notReady()
      if (invocation.rawInput.trim().length === 0) return { kind: 'error', text: 'A proposition is required. Usage: /prove <mathematical proposition>' }
      const message = await commandMessage('prove', invocation.rawInput)
      invocation.signal.throwIfAborted()
      invocation.agent.followup(message)
      return { kind: 'success', text: 'Queued the rigorous proof workflow.' }
    },
  })
  ctx.commands.register({
    name: 'audit-proof',
    description: 'audit a proof for invalid steps, gaps, assumptions, and counterexamples',
    input: { hint: '<proof text or math-rigor session id>' },
    handler: async invocation => {
      if (!isReady()) return notReady()
      if (invocation.rawInput.trim().length === 0) return { kind: 'error', text: 'A proof or session id is required. Usage: /audit-proof <proof text or session id>' }
      const message = await commandMessage('audit-proof', invocation.rawInput)
      invocation.signal.throwIfAborted()
      invocation.agent.followup(message)
      return { kind: 'success', text: 'Queued the proof-audit workflow.' }
    },
  })
}

async function apply(ctx, config = {}) {
  const home = resolve(expandHomePath(config.home || DEFAULT_HOME))
  let python
  try {
    python = await ensureEnvironment(home, config.python || '', config.setup, config.setupTimeoutMs)
  } catch (error) {
    const setupError = error instanceof Error ? error : new Error(String(error))
    ctx.logger.warn(`math-rigor: MCP tools are unavailable until setup completes: ${setupError.message}`)
  }

  registerCommands(ctx, () => python !== undefined, home)

  ctx.skills.registerProvider(() => {
    const candidate = {
      name: 'math-rigor',
      description: 'Use for rigorous, auditable mathematical proofs, derivations, verification, counterexamples, and proof audits with machine-checkable verdicts.',
      invocation: { modelInvocable: true, userInvocable: true },
      provider: name,
      source: 'bundled',
      rank: BUNDLED_SKILL_RANK,
      locator: pathToFileURL(SKILL_PATH),
      resourceBase: { kind: 'directory', path: SKILL_ROOT },
    }
    return {
      name,
      list: async () => [candidate],
      get: async selected => selected.name === candidate.name ? { ...candidate, content: await readSkill() } : undefined,
    }
  })

  if (python === undefined) return

  const mcpPlugin = {
    name: `${name}-mcp`,
    inject: mcpClientInject,
    Config: McpClientConfig,
    apply: applyMcpClient,
  }
  await ctx.plugin(mcpPlugin, {
    transport: 'stdio',
    serverName: 'math_rigor',
    command: python,
    args: [SERVER],
    cwd: ROOT,
    env: { MATH_RIGOR_HOME: home },
    toolCallTimeoutMs: config.toolCallTimeoutMs,
    failOnStartupError: true,
  })
  ctx.logger.info(`math-rigor: registered MCP tools through ${mcpClientName}; home=${home}`)
}

export { apply }
