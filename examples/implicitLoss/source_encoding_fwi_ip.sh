export LD_LIBRARY_PATH=/home/wangsw/anaconda3/lib:$LD_LIBRARY_PATH && \
python ../../codingfwi.py fullband.yml \
--gpuid 0 \
--opt adam \
--loss vp=implicit \
--mode inversion \
--batchsize 20 \
--lr vp=10.0 \
--save-path ./results_ip \
--checkpoint ./none \
--use-cuda \
--grad-cut
# --grad-smooth
