/**
 * 🔮 MAX Holographic Energy Core (v4 — full rebuild)
 *
 * Design: a self-illuminated fresnel-glow sphere with noise-driven surface
 * displacement, wrapped in rotating wireframe data-shells. Built as a raw
 * GLSL shaderMaterial instead of MeshTransmissionMaterial on purpose:
 * transmission needs real scene content behind it to refract (this app's
 * canvas is transparent over HTML chat UI, so there was nothing to bend —
 * root cause of the previous flat look). A self-lit shader has no such
 * dependency, works identically online/offline, and is far cheaper on
 * mobile GPUs.
 */
import { useRef, useMemo } from 'react'
import { useFrame } from '@react-three/fiber'
import { Float, Html } from '@react-three/drei'
import * as THREE from 'three'
import { motion } from 'framer-motion'

// ── STATE CONFIGURATION ──
const STATE_CONFIG = {
  idle: { colorA: '#52C8FF', colorB: '#2C6BFF', intensity: 0.09, floatSpeed: 1.1, coreSpeed: 0.25 },
  listening: { colorA: '#52C8FF', colorB: '#00E5C8', intensity: 0.16, floatSpeed: 1.8, coreSpeed: 0.55 },
  thinking: { colorA: '#8B5CF6', colorB: '#C77DFF', intensity: 0.22, floatSpeed: 2.6, coreSpeed: 0.85 },
  speaking: { colorA: '#D946EF', colorB: '#FF3FD7', intensity: 0.3, floatSpeed: 3.6, coreSpeed: 1.2 },
}

// ── Shader source ──
const VERTEX_SHADER = /* glsl */ `
  uniform float uTime;
  uniform float uIntensity;
  varying vec3 vNormal;
  varying vec3 vPosition;
  varying float vNoise;

  vec3 mod289(vec3 x){ return x - floor(x * (1.0/289.0)) * 289.0; }
  vec4 mod289(vec4 x){ return x - floor(x * (1.0/289.0)) * 289.0; }
  vec4 permute(vec4 x){ return mod289(((x*34.0)+1.0)*x); }
  vec4 taylorInvSqrt(vec4 r){ return 1.79284291400159 - 0.85373472095314 * r; }

  float snoise(vec3 v){
    const vec2 C = vec2(1.0/6.0, 1.0/3.0);
    const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
    vec3 i  = floor(v + dot(v, C.yyy));
    vec3 x0 = v - i + dot(i, C.xxx);
    vec3 g = step(x0.yzx, x0.xyz);
    vec3 l = 1.0 - g;
    vec3 i1 = min(g.xyz, l.zxy);
    vec3 i2 = max(g.xyz, l.zxy);
    vec3 x1 = x0 - i1 + C.xxx;
    vec3 x2 = x0 - i2 + C.yyy;
    vec3 x3 = x0 - D.yyy;
    i = mod289(i);
    vec4 p = permute(permute(permute(
              i.z + vec4(0.0, i1.z, i2.z, 1.0))
            + i.y + vec4(0.0, i1.y, i2.y, 1.0))
            + i.x + vec4(0.0, i1.x, i2.x, 1.0));
    float n_ = 0.142857142857;
    vec3 ns = n_ * D.wyz - D.xzx;
    vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
    vec4 x_ = floor(j * ns.z);
    vec4 y_ = floor(j - 7.0 * x_);
    vec4 x = x_ * ns.x + ns.yyyy;
    vec4 y = y_ * ns.x + ns.yyyy;
    vec4 h = 1.0 - abs(x) - abs(y);
    vec4 b0 = vec4(x.xy, y.xy);
    vec4 b1 = vec4(x.zw, y.zw);
    vec4 s0 = floor(b0) * 2.0 + 1.0;
    vec4 s1 = floor(b1) * 2.0 + 1.0;
    vec4 sh = -step(h, vec4(0.0));
    vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
    vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
    vec3 p0 = vec3(a0.xy, h.x);
    vec3 p1 = vec3(a0.zw, h.y);
    vec3 p2 = vec3(a1.xy, h.z);
    vec3 p3 = vec3(a1.zw, h.w);
    vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2,p2), dot(p3,p3)));
    p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
    vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
    m = m * m;
    return 42.0 * dot(m*m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
  }

  void main(){
    vNormal = normalize(normalMatrix * normal);
    float n = snoise(position * 1.7 + uTime * 0.28);
    vNoise = n;
    vec3 displaced = position + normal * n * uIntensity;
    vec4 worldPos = modelMatrix * vec4(displaced, 1.0);
    vPosition = worldPos.xyz;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(displaced, 1.0);
  }
`

const FRAGMENT_SHADER = /* glsl */ `
  uniform vec3 uColorA;
  uniform vec3 uColorB;
  varying vec3 vNormal;
  varying vec3 vPosition;
  varying float vNoise;

  void main(){
    vec3 viewDir = normalize(cameraPosition - vPosition);
    float fresnel = pow(1.0 - max(dot(viewDir, normalize(vNormal)), 0.0), 2.3);
    vec3 base = mix(uColorA, uColorB, 0.5 + 0.5 * vNoise);
    vec3 rim = base * fresnel * 2.4;
    vec3 core = base * 0.3;
    vec3 finalColor = core + rim;
    float alpha = clamp(fresnel * 1.35 + 0.22, 0.0, 1.0);
    gl_FragColor = vec4(finalColor, alpha);
  }
`

// ── Energy Core Shell (the main glowing sphere) ──
function EnergyShell({ state }) {
  const matRef = useRef()
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.idle

  const colorA = useMemo(() => new THREE.Color(cfg.colorA), [])
  const colorB = useMemo(() => new THREE.Color(cfg.colorB), [])
  const targetA = useMemo(() => new THREE.Color(), [])
  const targetB = useMemo(() => new THREE.Color(), [])

  const uniforms = useMemo(
    () => ({
      uTime: { value: 0 },
      uIntensity: { value: cfg.intensity },
      uColorA: { value: colorA },
      uColorB: { value: colorB },
    }),
    []
  )

  useFrame((s, delta) => {
    if (!matRef.current) return
    const u = matRef.current.uniforms
    u.uTime.value = s.clock.elapsedTime

    // Smooth cross-fade between states instead of a hard cut
    targetA.set(cfg.colorA)
    targetB.set(cfg.colorB)
    u.uColorA.value.lerp(targetA, delta * 2.2)
    u.uColorB.value.lerp(targetB, delta * 2.2)
    u.uIntensity.value = THREE.MathUtils.lerp(u.uIntensity.value, cfg.intensity, delta * 2.2)
  })

  return (
    <mesh>
      <sphereGeometry args={[1, 160, 160]} />
      <shaderMaterial
        ref={matRef}
        uniforms={uniforms}
        vertexShader={VERTEX_SHADER}
        fragmentShader={FRAGMENT_SHADER}
        transparent
        side={THREE.FrontSide}
        depthWrite={false}
        blending={THREE.AdditiveBlending}
      />
    </mesh>
  )
}

// ── Dense inner glow (gives the core solidity so it isn't just a rim outline) ──
function InnerGlow({ state }) {
  const ref = useRef()
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.idle
  useFrame((s) => {
    if (ref.current) {
      const breath = 1 + Math.sin(s.clock.elapsedTime * 1.1) * 0.05
      ref.current.scale.setScalar(0.62 * breath)
    }
  })
  return (
    <mesh ref={ref}>
      <sphereGeometry args={[1, 48, 48]} />
      <meshBasicMaterial color={cfg.colorA} transparent opacity={0.35} toneMapped={false} />
    </mesh>
  )
}

// ── Wireframe Data Shells (holographic HUD structure around the core) ──
function DataShell({ radius, speed, opacity, color, detail = 1 }) {
  const ref = useRef()
  useFrame((_, delta) => {
    if (ref.current) {
      ref.current.rotation.y += delta * speed
      ref.current.rotation.x += delta * speed * 0.4
    }
  })
  return (
    <mesh ref={ref}>
      <icosahedronGeometry args={[radius, detail]} />
      <meshBasicMaterial color={color} wireframe transparent opacity={opacity} toneMapped={false} />
    </mesh>
  )
}

// ── Scan Ring (equatorial HUD ring, replaces the old "planet ring" look) ──
function ScanRing({ state }) {
  const ref = useRef()
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.idle
  useFrame((_, delta) => {
    if (ref.current) ref.current.rotation.z += delta * 0.3
  })
  return (
    <mesh ref={ref} rotation-x={Math.PI / 2.15}>
      <ringGeometry args={[1.32, 1.34, 128]} />
      <meshBasicMaterial color={cfg.colorA} transparent opacity={0.3} side={THREE.DoubleSide} toneMapped={false} />
    </mesh>
  )
}

// ── Neural Particles (orbiting motes) ──
function NeuralParticles({ count = 36 }) {
  const particles = useMemo(
    () =>
      Array.from({ length: count }).map(() => ({
        x: (Math.random() - 0.5) * 2.1,
        y: (Math.random() - 0.5) * 2.1,
        z: (Math.random() - 0.5) * 2.1,
      })),
    [count]
  )
  const ref = useRef()
  useFrame((s) => {
    if (ref.current) {
      ref.current.rotation.y = s.clock.elapsedTime * 0.06
      ref.current.rotation.x = Math.sin(s.clock.elapsedTime * 0.12) * 0.12
    }
  })
  return (
    <group ref={ref}>
      {particles.map((p, i) => (
        <mesh key={i} position={[p.x, p.y, p.z]}>
          <sphereGeometry args={[0.014, 6, 6]} />
          <meshBasicMaterial color="#ffffff" transparent opacity={0.55} toneMapped={false} />
        </mesh>
      ))}
    </group>
  )
}

// ── Floating Dust (ambient depth) ──
function FloatingDust({ count = 20 }) {
  const pos = useMemo(() => {
    const p = new Float32Array(count * 3)
    for (let i = 0; i < count; i++) {
      p[i * 3] = (Math.random() - 0.5) * 6
      p[i * 3 + 1] = (Math.random() - 0.5) * 6
      p[i * 3 + 2] = (Math.random() - 0.5) * 2
    }
    return p
  }, [count])
  const ref = useRef()
  useFrame((_, delta) => {
    if (ref.current) {
      ref.current.position.y += delta * 0.05
      if (ref.current.position.y > 3) ref.current.position.y = -3
    }
  })
  return (
    <points ref={ref}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" count={count} array={pos} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial color="#ffffff" size={0.028} transparent opacity={0.18} />
    </points>
  )
}

// ── Pulse Ring (HTML overlay ping) ──
function PulseRing() {
  return (
    <Html center>
      <motion.div
        animate={{ scale: [1, 1.5], opacity: [0.12, 0] }}
        transition={{ duration: 1.8, repeat: Infinity, repeatDelay: 6, ease: 'easeOut' }}
        style={{
          width: '240px',
          height: '240px',
          border: '2px solid rgba(82, 200, 255, 0.5)',
          borderRadius: '50%',
          position: 'absolute',
          top: '-120px',
          left: '-120px',
          pointerEvents: 'none',
        }}
      />
    </Html>
  )
}

// ── Background Aura (HTML, sits behind the canvas) ──
function AuraLayers({ state }) {
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.idle
  return (
    <Html center zIndexRange={[-10, -5]}>
      <motion.div
        animate={{ scale: [1, 1.08, 1] }}
        transition={{ duration: 14, repeat: Infinity, ease: 'easeInOut' }}
        style={{
          width: '380px',
          height: '380px',
          position: 'absolute',
          top: '-190px',
          left: '-190px',
          background:
            'radial-gradient(circle, rgba(82,200,255,0.05) 0%, rgba(139,92,246,0.02) 50%, transparent 80%)',
          filter: 'blur(80px)',
          pointerEvents: 'none',
        }}
      />
      <motion.div
        animate={{ backgroundColor: cfg.colorA }}
        transition={{ duration: 1 }}
        style={{
          width: '300px',
          height: '300px',
          position: 'absolute',
          top: '-150px',
          left: '-150px',
          borderRadius: '50%',
          filter: 'blur(60px)',
          opacity: 0.06,
          pointerEvents: 'none',
        }}
      />
    </Html>
  )
}

// ── MAIN ORB EXPORT ──
export default function OrbCore({ state = 'idle' }) {
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.idle

  return (
    <group>
      <AuraLayers state={state} />
      <FloatingDust />

      <Float speed={cfg.floatSpeed} rotationIntensity={0.35} floatIntensity={0.55} floatingRange={[-0.1, 0.1]}>
        <group>
          <EnergyShell state={state} />
          <InnerGlow state={state} />
          <DataShell radius={1.22} speed={cfg.coreSpeed} opacity={0.28} color={cfg.colorA} detail={1} />
          <DataShell radius={1.34} speed={-cfg.coreSpeed * 0.7} opacity={0.18} color={cfg.colorB} detail={2} />
          <ScanRing state={state} />
          <NeuralParticles />
          <PulseRing />
        </group>
      </Float>

      {/* Soft ambient fill only — the shader is self-illuminated, no directional key
          light needed, which is what was causing the hard terminator line before */}
      <ambientLight intensity={0.6} />
    </group>
  )
}
