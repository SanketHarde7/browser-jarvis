/**
 * 🌀 15-Layer Animated Orb Engine (MAX PRD)
 */
import { useRef, useMemo } from 'react'
import { useFrame } from '@react-three/fiber'
import { Sphere, MeshDistortMaterial, Html } from '@react-three/drei'
import * as THREE from 'three'
import { motion } from 'framer-motion'

// ── STATE CONFIGURATION (PRD colors) ──
const STATE_CONFIG = {
  idle: {
    color: '#52C8FF', // Primary Glow
    coreColor: '#ffffff',
    emissive: '#103050',
    distort: 0.15,
    speed: 0.5,
    scale: 0.85,
    ringSpeed: 0.4,
  },
  listening: {
    color: '#52C8FF',
    coreColor: '#ffffff',
    emissive: '#206080',
    distort: 0.25,
    speed: 1.2,
    scale: 0.9,
    ringSpeed: 0.8,
  },
  thinking: {
    color: '#8B5CF6', // Secondary Glow
    coreColor: '#e0d0ff',
    emissive: '#301050',
    distort: 0.2,
    speed: 0.8,
    scale: 0.88,
    ringSpeed: 1.5,
  },
  speaking: {
    color: '#D946EF', // Accent Glow
    coreColor: '#ffffff',
    emissive: '#501040',
    distort: 0.35,
    speed: 2.0,
    scale: 0.92,
    ringSpeed: 1.2,
  },
}

// ── LAYER 3: Energy Halo ──
function EnergyHalo({ state }) {
  const ref = useRef()
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.idle

  useFrame((_, delta) => {
    if (ref.current) ref.current.rotation.z += delta * 0.14 // ~45s rotation
  })

  return (
    <mesh ref={ref}>
      <ringGeometry args={[1.35, 1.36, 128]} />
      <meshBasicMaterial color={cfg.color} transparent opacity={0.22} side={THREE.DoubleSide} />
    </mesh>
  )
}

// ── LAYERS 5, 6, 7: Plasma Loops ──
function PlasmaLoops({ state }) {
  const loopA = useRef()
  const loopB = useRef()
  const loopC = useRef()
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.idle

  useFrame((s, delta) => {
    if (loopA.current) {
      loopA.current.rotation.x += delta * 0.18 // ~35s
      loopA.current.rotation.y = Math.sin(s.clock.elapsedTime * 0.5) * 0.2
    }
    if (loopB.current) {
      loopB.current.rotation.y -= delta * 0.22 // ~28s
      loopB.current.rotation.z = Math.cos(s.clock.elapsedTime * 0.4) * 0.3
    }
    if (loopC.current) {
      // Oscillating left and right while rotating
      loopC.current.rotation.z = Math.sin(s.clock.elapsedTime * 0.15) * 1.5
      loopC.current.rotation.x += delta * 0.15 // ~40s
    }
  })

  return (
    <group>
      {/* Loop A: Logic */}
      <mesh ref={loopA}>
        <torusGeometry args={[1.05, 0.015, 16, 100]} />
        <meshBasicMaterial color="#52C8FF" transparent opacity={0.4} />
      </mesh>
      {/* Loop B: Creativity */}
      <mesh ref={loopB}>
        <torusGeometry args={[1.1, 0.012, 16, 100]} />
        <meshBasicMaterial color="#8B5CF6" transparent opacity={0.3} />
      </mesh>
      {/* Loop C: Curiosity */}
      <mesh ref={loopC}>
        <torusGeometry args={[1.15, 0.012, 16, 100]} />
        <meshBasicMaterial color="#D946EF" transparent opacity={0.25} />
      </mesh>
    </group>
  )
}

// ── LAYER 9: Lumen Core ──
function LumenCore({ state }) {
  const ref = useRef()
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.idle

  useFrame((s) => {
    if (ref.current) {
      // Breathing animation (5.5s cycle)
      const breath = 1 + Math.sin(s.clock.elapsedTime * 1.14) * 0.03
      ref.current.scale.setScalar(0.28 * breath)
    }
  })

  return (
    <mesh ref={ref}>
      <sphereGeometry args={[1, 64, 64]} />
      <meshBasicMaterial color={cfg.coreColor} transparent opacity={0.9} />
    </mesh>
  )
}

// ── LAYER 10: Neural Particles ──
function NeuralParticles({ count = 50 }) {
  const particles = useMemo(() => {
    return Array.from({ length: count }).map(() => ({
      x: (Math.random() - 0.5) * 1.8,
      y: (Math.random() - 0.5) * 1.8,
      z: (Math.random() - 0.5) * 1.8,
      speed: Math.random() * 0.5 + 0.2,
      offset: Math.random() * Math.PI * 2
    }))
  }, [count])

  const ref = useRef()

  useFrame((s) => {
    if (ref.current) {
      ref.current.rotation.y = s.clock.elapsedTime * 0.05
      ref.current.rotation.x = Math.sin(s.clock.elapsedTime * 0.1) * 0.1
    }
  })

  return (
    <group ref={ref}>
      {particles.map((p, i) => (
        <mesh key={i} position={[p.x, p.y, p.z]}>
          <sphereGeometry args={[0.015, 8, 8]} />
          <meshBasicMaterial color="#ffffff" transparent opacity={0.6} />
        </mesh>
      ))}
    </group>
  )
}

// ── LAYER 11: Pulse Ring (HTML Overlay for absolute precision) ──
function PulseRing() {
  return (
    <Html center>
      <motion.div
        animate={{ scale: [1, 1.5], opacity: [0.12, 0] }}
        transition={{
          duration: 1.8,
          repeat: Infinity,
          repeatDelay: 8, // Randomize in real impl, fixed here for simplicity
          ease: 'easeOut'
        }}
        style={{
          width: '240px',
          height: '240px',
          border: '2px solid rgba(82, 200, 255, 0.5)',
          borderRadius: '50%',
          position: 'absolute',
          top: '-120px',
          left: '-120px',
          pointerEvents: 'none'
        }}
      />
    </Html>
  )
}

// ── LAYER 14: Floating Dust ──
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
      <pointsMaterial color="#ffffff" size={0.03} transparent opacity={0.2} />
    </points>
  )
}

// ── LAYER 1 & 2 & 15: HTML Auras and Glows ──
function AuraLayers({ state }) {
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.idle
  return (
    <Html center zIndexRange={[-10, -5]}>
      {/* Layer 1: Background Aura */}
      <motion.div
        animate={{ scale: [1, 1.08, 1] }}
        transition={{ duration: 14, repeat: Infinity, ease: 'easeInOut' }}
        style={{
          width: '360px',
          height: '360px',
          position: 'absolute',
          top: '-180px',
          left: '-180px',
          background: 'radial-gradient(circle, rgba(82, 200, 255, 0.12) 0%, rgba(139, 92, 246, 0.05) 50%, transparent 80%)',
          filter: 'blur(80px)',
          pointerEvents: 'none',
        }}
      />
      {/* Layer 2: Outer Atmospheric Glow (State adaptive) */}
      <motion.div
        animate={{ backgroundColor: cfg.color }}
        transition={{ duration: 1 }}
        style={{
          width: '300px',
          height: '300px',
          position: 'absolute',
          top: '-150px',
          left: '-150px',
          borderRadius: '50%',
          filter: 'blur(60px)',
          opacity: 0.18,
          pointerEvents: 'none',
        }}
      />
    </Html>
  )
}

// ── MAIN ORB EXPORT ──
export default function OrbCore({ state = 'idle' }) {
  const orbRef = useRef()
  const cfg = STATE_CONFIG[state] || STATE_CONFIG.idle

  useFrame((s, delta) => {
    if (orbRef.current) {
      // Layer 8: Nebula Flow (simulated by distort rotation)
      orbRef.current.rotation.y += delta * 0.1
      orbRef.current.rotation.z += delta * 0.05

      // Breathing
      const breath = 1 + Math.sin(s.clock.elapsedTime * 1.1) * 0.02
      orbRef.current.scale.setScalar(cfg.scale * breath)
    }
  })

  return (
    <group>
      {/* HTML Background Layers */}
      <AuraLayers state={state} />

      {/* 3D Layers */}
      <FloatingDust />
      <EnergyHalo state={state} />
      <PlasmaLoops state={state} />
      <NeuralParticles />
      <LumenCore state={state} />
      
      {/* HTML Foreground Layers */}
      <PulseRing />

      {/* Layer 4 & 13: Liquid Glass Shell (Main distort sphere) */}
      <Sphere ref={orbRef} args={[1, 128, 128]}>
        <MeshDistortMaterial
          color={cfg.color}
          emissive={cfg.emissive}
          emissiveIntensity={0.6}
          distort={cfg.distort}
          speed={cfg.speed}
          roughness={0.05} // Highly reflective glass
          metalness={0.9}
          transparent
          opacity={0.4}
          envMapIntensity={2.0}
        />
      </Sphere>

      {/* Lighting for Reflections */}
      <ambientLight intensity={0.4} />
      <directionalLight position={[5, 5, 5]} intensity={2} color="#ffffff" />
      <pointLight position={[-5, -5, 2]} intensity={1.5} color={cfg.color} />
    </group>
  )
}