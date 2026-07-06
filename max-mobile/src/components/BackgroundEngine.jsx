import React, { useMemo } from 'react';
import { motion } from 'framer-motion';

const BackgroundEngine = ({ maxState }) => {
  // Determine dominant colors based on maxState
  // Idle: Soft Blue, Listening: Bright Blue, Thinking: Purple, Speaking: Blue/Pink
  const getGlowColor = () => {
    switch (maxState) {
      case 'listening': return 'rgba(82, 200, 255, 0.15)'; // Bright Blue
      case 'thinking': return 'rgba(139, 92, 246, 0.12)'; // Purple
      case 'speaking': return 'rgba(217, 70, 239, 0.1)'; // Pink mix
      case 'offline': return 'rgba(255, 255, 255, 0.02)'; // Cool Gray
      default: return 'rgba(82, 200, 255, 0.05)'; // Soft Blue (Idle)
    }
  };

  // Pre-generate stars for Layer 4
  const stars = useMemo(() => {
    return Array.from({ length: 80 }).map((_, i) => ({
      id: i,
      x: Math.random() * 100,
      y: Math.random() * 100,
      size: Math.random() * 2 + 1,
      opacity: Math.random() * 0.5 + 0.1,
      duration: Math.random() * 20 + 20,
    }));
  }, []);

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 0, overflow: 'hidden', backgroundColor: '#05070B', pointerEvents: 'none' }}>
      
      {/* Layer 1: Radial Depth Gradient */}
      <motion.div 
        initial={{ opacity: 0 }}
        animate={{ opacity: 0.08 }}
        transition={{ duration: 2 }}
        style={{
          position: 'absolute',
          inset: '-50%',
          background: 'radial-gradient(circle at center, #111827 0%, transparent 70%)',
        }}
      />

      {/* Layer 2: Aurora Fog */}
      <motion.div 
        animate={{ 
          backgroundColor: getGlowColor(),
          x: ['-5%', '5%', '-5%'],
          y: ['-5%', '5%', '-5%'],
        }}
        transition={{ 
          backgroundColor: { duration: 1.5, ease: 'easeInOut' },
          x: { duration: 30, repeat: Infinity, ease: 'linear' },
          y: { duration: 45, repeat: Infinity, ease: 'linear' }
        }}
        style={{
          position: 'absolute',
          width: '150%',
          height: '150%',
          top: '-25%',
          left: '-25%',
          filter: 'blur(120px)',
          opacity: maxState === 'offline' ? 0 : 0.8,
        }}
      />

      {/* Layer 3: Volumetric Mist (Two opposing flows) */}
      <motion.div 
        animate={{ y: ['10%', '-10%'] }}
        transition={{ duration: 90, repeat: Infinity, ease: 'linear', repeatType: 'mirror' }}
        style={{
          position: 'absolute',
          inset: '-20%',
          background: 'linear-gradient(to top, rgba(82, 200, 255, 0.02), transparent)',
          filter: 'blur(150px)',
        }}
      />

      {/* Layer 4: Star Field */}
      <div style={{ position: 'absolute', inset: 0 }}>
        {stars.map(star => (
          <motion.div
            key={star.id}
            animate={{ 
              opacity: [star.opacity, star.opacity * 0.2, star.opacity],
              y: ['0%', '-5%']
            }}
            transition={{
              opacity: { duration: star.duration * 0.2, repeat: Infinity, repeatType: 'reverse' },
              y: { duration: star.duration, repeat: Infinity, ease: 'linear' }
            }}
            style={{
              position: 'absolute',
              left: `${star.x}%`,
              top: `${star.y}%`,
              width: star.size,
              height: star.size,
              backgroundColor: '#fff',
              borderRadius: '50%',
            }}
          />
        ))}
      </div>

      {/* Layer 9: Noise Texture */}
      <div 
        style={{
          position: 'absolute',
          inset: 0,
          opacity: 0.02,
          backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")',
          mixBlendMode: 'overlay',
        }}
      />
    </div>
  );
};

export default BackgroundEngine;
