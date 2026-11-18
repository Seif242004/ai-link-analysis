[nvidia:z-ai/glm-5.3-flash] https://www.python.org/ run 0: LLM provider 'nvidia' failed after retries: ReadTimeout: 
[nvidia:z-ai/glm-5.3-flash] https://fastapi.tiangolo.com/ run 0: LLM provider 'nvidia' failed after retries: ReadTimeout: 
[nvidia:google/gemma-4-31b-it] https://www.python.org/ run 0: LLM provider 'nvidia' failed after retries: ReadTimeout: 
[nvidia:google/gemma-4-31b-it] https://fastapi.tiangolo.com/ run 0: LLM provider 'nvidia' failed after retries: ReadTimeout: 

| Model | Runs | Valid JSON/schema | Median latency (s) | Avg in tok | Avg out tok | Avg cost / analysis |
|---|---|---|---|---|---|---|
| nvidia:nvidia/nemotron-3.5-lightning-30b-a3b | 2 | 2/2 | 15.3 | 2942 | 2227 | n/a |
| nvidia:z-ai/glm-5.3-flash | 2 | no successful runs | - | - | - | - |
| nvidia:google/gemma-4-31b-it | 2 | no successful runs | - | - | - | - |
