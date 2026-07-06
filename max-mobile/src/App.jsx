import React, { useState, useEffect, useRef, useCallback } from 'react';
import { motion } from 'framer-motion';
import { Mic, MicOff, MonitorUp, Settings } from 'lucide-react';
import './index.css';

import { SpeechRecognition } from '@capacitor-community/speech-recognition';

function App() {
  const [status, setStatus] = useState("disconnected");
  const [transcript, setTranscript] = useState("");
  const [maxReply, setMaxReply] = useState("");
  const [isMuted, setIsMuted] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  
  const [serverUrl, setServerUrl] = useState(() => localStorage.getItem("max_server_url") || "10.127.214.90:8000");
  const [showSettings, setShowSettings] = useState(false);
  
  const wsRef = useRef(null);
  const audioContextRef = useRef(null);
  const statusRef = useRef(status);
  const isMutedRef = useRef(isMuted);
  const startRecognitionRef = useRef(null);

  // Keep refs in sync with state
  useEffect(() => { statusRef.current = status; }, [status]);
  useEffect(() => { isMutedRef.current = isMuted; }, [isMuted]);
  
  // Request microphone permissions on load
  useEffect(() => {
    const initPermissions = async () => {
      try {
        const { speechRecognition } = await SpeechRecognition.checkPermissions();
        if (speechRecognition !== 'granted') {
          await SpeechRecognition.requestPermissions();
        }
      } catch (e) {
        console.error("Failed to request speech recognition permissions", e);
      }
    };
    initPermissions();
  }, []);

  // Initialize WebSocket
  useEffect(() => {
    let reconnectTimer;
    const connectWs = () => {
      try {
        if (wsRef.current) {
            wsRef.current.close();
        }
        const wsUrl = `ws://${serverUrl}/ws`;
        wsRef.current = new WebSocket(wsUrl);
        
        wsRef.current.onopen = () => {
          console.log("Connected to MAX Server at", wsUrl);
          setWsConnected(true);
          setStatus("connected");
        };
        
        wsRef.current.onmessage = (event) => {
          const msg = JSON.parse(event.data);
          
          if (msg.event === "response_text" && msg.text) {
            setMaxReply(msg.text);
            setStatus("speaking");
          } else if (msg.event === "audio_response" && msg.audio) {
            playAudioFromBase64(msg.audio);
          } else if (msg.event === "SWITCH_ACTIVE") {
             if (msg.device === "laptop") {
               setStatus("transferred");
               setTranscript("Control transferred to laptop.");
               SpeechRecognition.stop().catch(() => {});
             } else if (msg.device === "phone") {
               setStatus("connected");
               setTranscript("Phone is now active.");
               setTimeout(() => {
                 if (startRecognitionRef.current) startRecognitionRef.current();
               }, 300);
             }
          }
        };
        
        wsRef.current.onclose = () => {
          setWsConnected(false);
          setStatus("disconnected");
          reconnectTimer = setTimeout(connectWs, 3000);
        };
      } catch (err) {
        console.error("WebSocket error", err);
      }
    };
    
    connectWs();
    return () => {
        clearTimeout(reconnectTimer);
        wsRef.current?.close();
    };
  }, [serverUrl]);

  // Play audio chunks
  const playAudioFromBase64 = useCallback(async (base64String) => {
    try {
      const binaryString = window.atob(base64String);
      const len = binaryString.length;
      const bytes = new Uint8Array(len);
      for (let i = 0; i < len; i++) {
        bytes[i] = binaryString.charCodeAt(i);
      }
      
      if (!audioContextRef.current) {
        audioContextRef.current = new (window.AudioContext || window.webkitAudioContext)();
      }
      
      const audioBuffer = await audioContextRef.current.decodeAudioData(bytes.buffer);
      const source = audioContextRef.current.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(audioContextRef.current.destination);
      source.start(0);
      
      source.onended = () => {
        setStatus("listening");
        startRecognition();
      };
    } catch (error) {
      console.error("Audio play error", error);
    }
  }, []);

  const isStartingRef = useRef(false);

  // Capacitor Speech Recognition
  const startRecognition = useCallback(async () => {
    if (isMutedRef.current || statusRef.current === "transferred" || isStartingRef.current) return;
    
    try {
      isStartingRef.current = true;
      const { available } = await SpeechRecognition.available();
      if (!available) {
        setTranscript("Speech recognition not available on this phone. Please install Google App.");
        isStartingRef.current = false;
        return;
      }

      const { speechRecognition } = await SpeechRecognition.checkPermissions();
      if (speechRecognition !== 'granted') {
        setTranscript("Microphone permission denied.");
        isStartingRef.current = false;
        return;
      }
      
      const { listening } = await SpeechRecognition.isListening();
      if (listening) {
        isStartingRef.current = false;
        return;
      }
      
      // Clear old listeners to avoid duplicates
      await SpeechRecognition.removeAllListeners();
      
      SpeechRecognition.addListener('partialResults', (data) => {
        if (data.matches && data.matches.length > 0) {
          setTranscript(data.matches[0]);
        }
      });
      
      SpeechRecognition.addListener('listeningState', (data) => {
        if (data.status === 'started') {
           setStatus("listening");
        } else if (data.status === 'stopped') {
           // When user stops speaking, get the final text from the state and send it
           setTranscript((currentTranscript) => {
             // Ignore system messages from being sent to the AI
             if (currentTranscript && !currentTranscript.startsWith("Control transferred") && wsRef.current?.readyState === WebSocket.OPEN) {
               setStatus("processing");
               wsRef.current.send(JSON.stringify({
                 type: "text",
                 text: currentTranscript
               }));
             } else if (!isMutedRef.current && statusRef.current !== "transferred" && statusRef.current !== "processing") {
               // Restart if no text was captured and we aren't doing anything else
               setTimeout(() => {
                 if (statusRef.current === "listening" || statusRef.current === "connected") {
                    startRecognition();
                 }
               }, 500);
             }
             return currentTranscript; // Keep the transcript visible while processing
           });
        }
      });
      
      // Clear transcript before starting
      setTranscript("");
      await SpeechRecognition.start({
        language: 'en-US',
        maxResults: 1,
        prompt: 'Say something',
        partialResults: true,
        popup: false,
      });
      
    } catch (e) {
      console.error("Speech Recognition Error", e);
      setTranscript("Error: " + (e.message || JSON.stringify(e)));
    } finally {
      isStartingRef.current = false;
    }
  }, []);

  // Sync startRecognition with ref for websocket usage
  useEffect(() => {
    startRecognitionRef.current = startRecognition;
  }, [startRecognition]);

  // Toggle Mute
  const toggleMute = () => {
    setIsMuted(!isMuted);
    if (!isMuted) {
      SpeechRecognition.stop().catch(() => {});
      setStatus("muted");
    } else {
      setStatus("connected");
      setTimeout(() => startRecognition(), 300);
    }
  };

  // Switch to laptop command
  const switchToLaptop = () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: "text",
        text: "switch to laptop"
      }));
    }
  };

  // Determine orb animation state
  const getOrbVariants = () => {
    switch (status) {
      case "listening":
        return { scale: [1, 1.05, 1], transition: { repeat: Infinity, duration: 2, ease: "easeInOut" } };
      case "processing":
        return { scale: [1, 1.2, 1], rotate: 360, transition: { repeat: Infinity, duration: 1.5, ease: "linear" } };
      case "speaking":
        return { scale: [1.1, 1.3, 1.1], opacity: [0.8, 1, 0.8], transition: { repeat: Infinity, duration: 0.5 } };
      case "disconnected":
      case "transferred":
      case "muted":
        return { scale: 0.9, opacity: 0.3 };
      default:
        return { scale: 1 };
    }
  };

  return (
    <div className="flex flex-col items-center justify-between h-screen w-full pb-10 pt-16 relative overflow-hidden bg-background">
      {/* Top Status */}
      <div className="absolute top-6 left-6 flex items-center justify-between w-[calc(100%-3rem)]">
        <div className="flex items-center space-x-2">
          <div className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-green-500' : 'bg-red-500'}`} />
          <span className="text-xs text-white/50 tracking-widest uppercase font-medium">
            {wsConnected ? 'Connected' : 'Offline'}
          </span>
        </div>
        <button 
          onClick={() => setShowSettings(true)}
          className="p-2 rounded-full glass-panel hover:bg-white/10"
        >
          <Settings className="w-5 h-5 text-white/50" />
        </button>
      </div>

      {/* Main Orb Visualizer */}
      <div className="flex-1 flex items-center justify-center w-full">
        <motion.div 
          animate={getOrbVariants()}
          className="w-48 h-48 rounded-full orb-glow bg-gradient-to-br from-indigo-500/20 to-purple-500/20 backdrop-blur-3xl border border-white/10"
        />
      </div>

      {/* Glassmorphism Chat Panel */}
      <div className="w-[90%] max-w-md mx-auto p-6 rounded-3xl glass-panel flex flex-col space-y-4 mb-8">
        <div className="min-h-[80px]">
          {maxReply ? (
            <p className="text-white text-lg font-medium leading-relaxed">{maxReply}</p>
          ) : (
            <p className="text-white/40 italic">Awaiting response...</p>
          )}
        </div>
        
        <div className="h-px w-full bg-white/10" />
        
        <p className="text-white/60 text-sm h-12 overflow-hidden">
          {transcript || (status === 'listening' ? "Listening..." : "Microphone paused.")}
        </p>
      </div>

      {/* Action Buttons */}
      <div className="flex items-center space-x-6">
        <button 
          onClick={toggleMute}
          className="p-4 rounded-full glass-panel hover:bg-white/10 transition-colors active:scale-95"
        >
          {isMuted ? <MicOff className="w-6 h-6 text-white/60" /> : <Mic className="w-6 h-6 text-white" />}
        </button>
        
        <button 
          onClick={switchToLaptop}
          className="px-6 py-4 rounded-full glass-panel flex items-center space-x-3 hover:bg-white/10 transition-colors active:scale-95"
        >
          <MonitorUp className="w-5 h-5 text-indigo-400" />
          <span className="text-white text-sm font-medium">Laptop</span>
        </button>
      </div>

      {/* Settings Modal */}
      {showSettings && (
        <div className="absolute inset-0 bg-black/80 flex items-center justify-center p-6 z-50">
          <div className="glass-panel p-6 rounded-2xl w-full max-w-sm flex flex-col space-y-4">
            <h3 className="text-white text-lg font-medium">Connection Settings</h3>
            <p className="text-white/50 text-xs">Enter MAX server IP or hostname (e.g. max-server.local:8000 or 10.x.x.x:8000)</p>
            <input 
              type="text"
              value={serverUrl}
              onChange={(e) => setServerUrl(e.target.value)}
              className="bg-black/50 border border-white/10 rounded-lg p-3 text-white outline-none focus:border-indigo-500"
            />
            <div className="flex space-x-3 pt-2">
              <button 
                onClick={() => setShowSettings(false)}
                className="flex-1 py-3 rounded-xl bg-white/5 text-white hover:bg-white/10"
              >
                Cancel
              </button>
              <button 
                onClick={() => {
                  localStorage.setItem("max_server_url", serverUrl);
                  setShowSettings(false);
                  window.location.reload();
                }}
                className="flex-1 py-3 rounded-xl bg-indigo-500/80 text-white font-medium hover:bg-indigo-500"
              >
                Save & Restart
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
